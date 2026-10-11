"""A bounded, indeterminate activity animation; no invented completion percentage."""
import re
import math
import time
import tkinter as tk


class ActivityBar(tk.Canvas):
    def __init__(self, parent, **kwargs):
        super().__init__(parent, height=7, highlightthickness=0, borderwidth=0,
                         background="#111b27", **kwargs)
        self.timer = None
        self.started = 0
        self.result = None
        self.bind("<Configure>", lambda _: self.draw())
        self.bind("<Destroy>", lambda _: self.stop())

    def start(self, _interval=None):
        self.stop()
        self.result = None
        self.started = time.monotonic()
        self.tick()

    def stop(self):
        if self.timer is not None:
            self.after_cancel(self.timer)
            self.timer = None

    def finish(self, success):
        self.stop()
        self.result = success
        self.draw()

    def tick(self):
        self.draw()
        self.timer = self.after(32, self.tick)

    def draw(self):
        self.delete("all")
        width, height = self.winfo_width(), self.winfo_height()
        if self.result is not None:
            self.create_rectangle(0, 0, width, height, fill="#70dec6" if self.result else "#edc080", outline="")
            return
        if not self.started:
            return
        # Smoothly sweep a shaded pulse. Width expresses activity, never percent.
        center = width * (.5 - .65 * math.cos((time.monotonic() - self.started) * 2.1))
        span = max(28, width * .24)
        for index, color in enumerate(("#203e43", "#285650", "#347a6c", "#49a791", "#70dec6")):
            half = span * (1 - index * .16)
            self.create_rectangle(center - half, 0, center + half, height, fill=color, outline="")


def activity_phase(line: str) -> str | None:
    value = line.lower()
    if any(word in value for word in ("error:", "traceback", "failed")):
        return "Reviewing an update problem…"
    if any(word in value for word in ("[sourcetext]", "yugipedia", "codexygo", "ocg-card")):
        return "Checking card translations…"
    if any(word in value for word in ("download", "fetching", "upstream package", "git pull")):
        return "Fetching the latest cards…"
    if any(word in value for word in ("merged", "copied", "workspace", "sync")):
        return "Updating the translation workspace…"
    if any(word in value for word in ("build", "packaging", ".ypk")):
        return "Preparing card packages…"
    if any(word in value for word in ("push", "commit", "upload")):
        return "Publishing the selected workspace…"
    return None


def activity_result(line: str):
    match = re.search(r"candidates=(\d+), matched=(\d+)", line)
    if match:
        return "Matched {matched} of {candidates} source cards.", dict(candidates=match[1], matched=match[2])
    match = re.search(r"Updated CDB rows=(\d+)", line)
    if match:
        return "Updated {count} card texts.", dict(count=match[1])
    match = re.search(r"Rush ready: (\d+) cards; (\d+) added, (\d+) retired", line)
    if match:
        return "Rush: {cards} cards, {added} added, {removed} removed.", dict(cards=match[1], added=match[2], removed=match[3])
    return None
