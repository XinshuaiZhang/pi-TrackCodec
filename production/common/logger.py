"""Operation log"""
import os, json
from datetime import datetime


class OperationLogger:
    def __init__(self, log_path: str):
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        self.path = log_path
        if not os.path.exists(log_path):
            with open(self.path, 'w') as f:
                f.write("# Operation log\n\n")

    def log(self, operation: str, command: str = '', result: str = 'success', **kwargs):
        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        entry = f"## {ts}\n- operation: {operation}\n"
        if command:
            entry += f"- command: {command}\n"
        if kwargs:
            entry += f"- details: {json.dumps(kwargs, ensure_ascii=False, default=str)}\n"
        entry += f"- result: {result}\n\n"
        with open(self.path, 'a') as f:
            f.write(entry)

    def __call__(self, operation: str, command: str = '', result: str = 'success', **kwargs):
        self.log(operation, command, result, **kwargs)
