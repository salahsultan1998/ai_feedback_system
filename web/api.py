"""Read-only configuration; uploads and report creation are explicit actions."""
from datetime import timedelta
import json
import os
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request, send_file, session, url_for
import markdown
from werkzeug.exceptions import HTTPException

from feedback.config import load_config
from feedback.importer import read_feedback
from feedback.cleaning import clean_feedback, parse_dates
from feedback.storage import atomic_csv, atomic_json, read_csv
from feedback.workflow import prepare_selection
from feedback.jobs import BusyError

api = Blueprint("api", __name__, url_prefix="/api")


def services():
    return current_app.extensions["storage"], current_app.extensions["jobs"]


def config_for(game):
    return load_config(game, current_app.config["PROJECT_ROOT"])


@api.before_request
def csrf():
    if request.method == "POST" and request.headers.get("X-CSRF-Token") != session.get("csrf", "missing"):
        return jsonify(error="页面已过期，请刷新后重试。"), 403


@api.errorhandler(ValueError)
def invalid(error):
    return jsonify(error=str(error)), 409 if isinstance(error, BusyError) else 400


@api.errorhandler(Exception)
def unexpected(error):
    if isinstance(error, HTTPException):
        return jsonify(error=error.description), error.code
    current_app.logger.exception("API request failed")
    return jsonify(error="无法完成请求，请检查文件格式或服务日志。"), 500


@api.post("/games/<game>/uploads")
def upload(game):
    config = config_for(game)
    file = request.files.get("file")
    suffix = Path(file.filename or "").suffix.lower() if file else ""
    if suffix not in {".csv", ".xlsx"}:
        raise ValueError("请选择 CSV 或 .xlsx 文件。")
    storage, _ = services()
    run, folder = storage.create(game)
    source = folder / ("source" + suffix)
    file.save(source)
    raw = read_feedback(source, config)
    cleaned, excluded = clean_feedback(raw, config)
    if cleaned.empty:
        raise ValueError("清洗后没有有效反馈，请检查文件。")
    atomic_csv(folder / "rows.csv", raw)
    dates = parse_dates(raw["反馈时间"]).dropna()
    end = cleaned["反馈时间"].max().date()
    meta = {"run": run, "game": game, "filename": file.filename, "total": len(raw),
            "valid": len(cleaned), "excluded": len(excluded),
            "source_start": str(dates.min().date()), "source_end": str(dates.max().date()),
            "suggested_start": str(end - timedelta(days=6)), "suggested_end": str(end),
            "column_mapping": config["column_mapping"]}
    atomic_json(folder / "upload.json", meta)
    return jsonify(meta), 201


def load_run(game, run):
    config = config_for(game)
    storage, jobs = services()
    meta = storage.metadata(game, run)
    if meta["column_mapping"] != config["column_mapping"]:
        raise ValueError("列映射已变更，请重新上传文件。")
    return storage.folder(game, run), config, meta, jobs


@api.get("/games/<game>/uploads/<run>")
def upload_metadata(game, run):
    folder, config, meta, _ = load_run(game, run)
    latest = folder / "progress.json"
    return jsonify(**meta, latest_job=json.loads(latest.read_text())["job_id"] if latest.exists() else None)


@api.get("/games/<game>/uploads/<run>/preview")
def preview(game, run):
    folder, config, _, _ = load_run(game, run)
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        raise ValueError("页码无效。") from None
    raw = read_csv(folder / "rows.csv")
    size = config["preview_page_size"]
    first = (page - 1) * size
    return jsonify(columns=list(raw.columns), rows=raw.iloc[first:first + size].values.tolist(),
                   page=page, page_size=size, total=len(raw))


def options():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        raise ValueError("请求格式无效。")
    return {"start": data.get("start"), "end": data.get("end"), "confirmed": data.get("confirmed") is True}


@api.post("/games/<game>/uploads/<run>/selection")
def selection(game, run):
    folder, config, _, _ = load_run(game, run)
    _, info = prepare_selection(folder, config, **options())
    return jsonify(info)


@api.post("/games/<game>/uploads/<run>/reports")
def report(game, run):
    folder, config, _, jobs = load_run(game, run)
    selected = options()
    _, info = prepare_selection(folder, config, **selected)
    if not info["ready"]:
        raise ValueError("数据未覆盖两个期间，请补充数据或确认无记录日期确实没有反馈。")
    if not os.getenv("OPENROUTER_API_KEY"):
        raise ValueError("请先在项目根目录的 .env 中设置 OPENROUTER_API_KEY，再生成报告。")
    # Snapshot the environment model so a running job has stable processing settings.
    from feedback.ai_client import model_name
    config["model"] = model_name(config)
    return jsonify(jobs.start(game, run, config, selected)), 202


@api.get("/games/<game>/uploads/<run>/jobs/<job>")
def status(game, run, job):
    _, jobs = services()
    return jsonify(jobs.read(game, run, job))


@api.post("/games/<game>/uploads/<run>/jobs/<job>/stop")
def stop(game, run, job):
    _, jobs = services()
    return jsonify(jobs.stop(game, run, job))


def completed_folder(game, run, job):
    storage, jobs = services()
    data = jobs.read(game, run, job)
    if data["status"] != "complete":
        raise ValueError("此任务尚未完成。")
    return storage.folder(game, run) / "reports" / job


@api.get("/games/<game>/uploads/<run>/jobs/<job>/report")
def report_preview(game, run, job):
    folder = completed_folder(game, run, job)
    text = (folder / "report.md").read_text(encoding="utf-8")
    html = markdown.markdown(text, extensions=["tables"])
    # Export's relative link points to the matching downloadable workbook in browser preview.
    excel_url = url_for("api.download", game=game, run=run, job=job, kind="excel")
    html = html.replace('href="feedback_details.xlsx"', f'href="{excel_url}"')
    return jsonify(html=html)


@api.get("/games/<game>/uploads/<run>/jobs/<job>/download/<kind>")
def download(game, run, job, kind):
    folder = completed_folder(game, run, job)
    names = {"markdown": "report.md", "excel": "feedback_details.xlsx"}
    if kind not in names:
        raise ValueError("下载格式不存在。")
    return send_file(folder / names[kind], as_attachment=True)
