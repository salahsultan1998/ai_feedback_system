"""One report task at a time; persisted progress survives app restart."""
import json
import threading
import uuid

from .storage import atomic_json
from .workflow import run_report


class BusyError(ValueError):
    pass


class Jobs:
    def __init__(self, storage, logger):
        self.storage, self.logger = storage, logger
        self.lock = threading.RLock()
        self.active = None
        self.cancel = threading.Event()
        if storage.root.exists():
            for path in storage.root.glob("*/*/jobs/*.json"):
                data = json.loads(path.read_text(encoding="utf-8"))
                if data["status"] in {"running", "stopping"}:
                    data.update(status="interrupted", message="服务已重启。已完成的翻译已保存，请重新生成以继续。")
                    atomic_json(path, data)

    def path(self, game, run, job):
        # Validate job using the same strict identifier validation as a run.
        self.storage.folder(game, job)
        return self.storage.folder(game, run) / "jobs" / f"{job}.json"

    def read(self, game, run, job):
        with self.lock:
            path = self.path(game, run, job)
            if not path.exists():
                raise ValueError("任务不存在。")
            return json.loads(path.read_text(encoding="utf-8"))

    def start(self, game, run, config, options):
        with self.lock:
            if self.active:
                raise BusyError("另一个报告正在处理，请完成或停止后再试。")
            job = uuid.uuid4().hex
            data = dict(id=job, game=game, run=run, status="running", stage="processing",
                        message="正在准备反馈", done=0, total=0, result=None, options=options)
            path = self.path(game, run, job)
            atomic_json(path, data)
            folder = self.storage.folder(game, run)
            atomic_json(folder / "progress.json", {"job_id": job})
            self.active = job
            self.cancel.clear()

            def update(**values):
                with self.lock:
                    data.update(values)
                    if self.cancel.is_set() and data["status"] in {"running", "stopping"}:
                        data.update(status="stopping", message="正在停止；当前请求结束后保存结果。")
                    atomic_json(path, data)

            def worker():
                try:
                    result = run_report(folder, config, **options, job_id=job,
                                        progress=update, should_stop=self.cancel.is_set)
                    update(status="complete", stage="complete", message="报告已生成", result=result)
                except InterruptedError as error:
                    update(status="stopped", message=str(error))
                except ValueError as error:
                    self.logger.warning("Report task failed: job=%s reason=%s", job, error)
                    update(status="failed", message=str(error))
                except Exception:
                    self.logger.exception("Report task failed: %s", job)
                    update(status="failed", message="处理失败。已保存完成的翻译，请检查文件或服务日志后重试。")
                finally:
                    with self.lock:
                        self.active = None
            threading.Thread(target=worker, daemon=True).start()
            return dict(data)

    def stop(self, game, run, job):
        with self.lock:
            data = self.read(game, run, job)
            if self.active == job:
                self.cancel.set()
                data.update(status="stopping", message="正在停止；当前请求结束后保存结果。")
                atomic_json(self.path(game, run, job), data)
            return data
