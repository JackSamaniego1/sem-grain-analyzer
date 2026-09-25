"""UX-07: cooperative cancel stops an analysis within about a second, in
every detection mode, including in the middle of one image."""
import threading
import time
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from conftest import make_mosaic
from core.cancel import AnalysisCancelled, make_cancel_check
from core.grain_detector import (
    SAM_POINTS_PER_BATCH_CPU, DetectionParams, GrainDetector, run_sam_generator,
)

LATENCY_BUDGET_S = 1.0


@pytest.fixture(scope="module")
def big_bgr():
    # 2048x1536 (the performance-budget size), built by tiling a 512x512
    # mosaic -- generating it directly takes ~50 s.
    gray, _ = make_mosaic(512, 512, n_grains=70, seed=11)
    return cv2.cvtColor(np.tile(gray, (3, 4)), cv2.COLOR_GRAY2BGR)


def _cancel_at(big_bgr, mode, at_pct):
    """Run analyze, set the cancel event when progress reaches ``at_pct``;
    return seconds from the cancel request to the AnalysisCancelled."""
    ev = threading.Event()
    t_set = []

    def progress(pct, msg):
        if pct >= at_pct and not ev.is_set():
            t_set.append(time.perf_counter())
            ev.set()

    with pytest.raises(AnalysisCancelled):
        GrainDetector().analyze(big_bgr, 1.0, DetectionParams(detection_mode=mode),
                                progress_callback=progress, cancel=ev)
    assert t_set, "cancel was never requested"
    return time.perf_counter() - t_set[0]


@pytest.mark.parametrize("mode", ["threshold", "boundary"])
@pytest.mark.parametrize("at_pct", [5, 30, 42, 50, 78, 88, 94])
def test_cancel_mid_image_returns_within_budget(big_bgr, mode, at_pct):
    latency = _cancel_at(big_bgr, mode, at_pct)
    print(f"cancel latency {mode}@{at_pct}%: {latency * 1000:.0f} ms")
    assert latency < LATENCY_BUDGET_S, f"{mode}@{at_pct}%: {latency:.2f}s"


@pytest.mark.parametrize("mode", ["threshold", "boundary"])
def test_cancel_from_other_thread_mid_run(big_bgr, mode):
    ev = threading.Event()
    started = threading.Event()
    out = {}

    def run():
        try:
            GrainDetector().analyze(big_bgr, 1.0, DetectionParams(detection_mode=mode),
                                    progress_callback=lambda p, m: started.set(),
                                    cancel=ev)
            out["result"] = True
        except AnalysisCancelled:
            out["cancelled"] = time.perf_counter()

    th = threading.Thread(target=run)
    th.start()
    assert started.wait(10)
    time.sleep(0.15)
    t0 = time.perf_counter()
    ev.set()
    th.join(5)
    assert not th.is_alive()
    if "result" in out:           # finished before the cancel landed (very fast CPU)
        return
    assert out["cancelled"] - t0 < LATENCY_BUDGET_S


def test_no_cancel_token_gives_identical_result(mosaic_bgr):
    p = DetectionParams(detection_mode="boundary")
    a = GrainDetector().analyze(mosaic_bgr, 1.0, p)
    b = GrainDetector().analyze(mosaic_bgr, 1.0, p, cancel=threading.Event())
    assert a.grain_count == b.grain_count
    assert np.array_equal(a.label_image, b.label_image)


def test_callable_token_and_bad_token():
    make_cancel_check(lambda: False)()
    with pytest.raises(AnalysisCancelled):
        make_cancel_check(lambda: True)()
    with pytest.raises(TypeError):
        make_cancel_check(42)


# ----------------------------------------------------------------------
# SAM: cancel honoured between point batches and encoder blocks (mocked)
# ----------------------------------------------------------------------

class _FakeBlock:
    def __init__(self, log):
        self.log = log

    def forward(self, x):
        self.log.append("block")
        return x


class _FakeGenerator:
    """Mimics SamAutomaticMaskGenerator's control flow: encoder blocks,
    then one ``_process_batch`` per ``points_per_batch`` points."""

    def __init__(self, n_points, points_per_batch, log, on_batch=None):
        self.on_batch = on_batch
        self.points_per_batch = points_per_batch
        self.n_points = n_points
        self.log = log
        blocks = [_FakeBlock(log) for _ in range(12)]
        self.predictor = SimpleNamespace(
            model=SimpleNamespace(image_encoder=SimpleNamespace(blocks=blocks)))

    def _process_batch(self, i):
        self.log.append(("batch", i))
        if self.on_batch:
            self.on_batch()

    def generate(self, image):
        for blk in self.predictor.model.image_encoder.blocks:
            blk.forward(image)
        for i in range(0, self.n_points, self.points_per_batch):
            self._process_batch(i)
        return [{"segmentation": np.zeros((4, 4), bool), "area": 0}]


def test_sam_cancel_between_batches():
    log = []
    ev = threading.Event()

    def on_batch():                 # user presses Cancel during batch 3
        if sum(1 for e in log if isinstance(e, tuple)) == 3:
            ev.set()

    gen = _FakeGenerator(1024, SAM_POINTS_PER_BATCH_CPU, log, on_batch)
    with pytest.raises(AnalysisCancelled):
        run_sam_generator(gen, np.zeros((8, 8, 3), np.uint8), 1024,
                          lambda p, m: None, make_cancel_check(ev))
    batches = [e for e in log if isinstance(e, tuple)]
    assert len(batches) == 3                      # stopped at the next batch
    assert len(batches) < 1024 // SAM_POINTS_PER_BATCH_CPU
    # hooks removed: class methods visible again
    assert "_process_batch" not in gen.__dict__
    assert all("forward" not in b.__dict__
               for b in gen.predictor.model.image_encoder.blocks)


def test_sam_cancel_between_encoder_blocks():
    log = []
    gen = _FakeGenerator(1024, 16, log)
    ev = threading.Event()
    real_forward = _FakeBlock.forward

    def fwd(self, x):
        real_forward(self, x)
        if log.count("block") == 2:
            ev.set()
    _FakeBlock.forward = fwd
    try:
        with pytest.raises(AnalysisCancelled):
            run_sam_generator(gen, None, 1024, lambda p, m: None, make_cancel_check(ev))
    finally:
        _FakeBlock.forward = real_forward
    assert log.count("block") == 2
    assert not any(isinstance(e, tuple) for e in log)


def test_sam_runner_without_cancel_reports_progress():
    log = []
    gen = _FakeGenerator(256, 16, log)
    msgs = []
    masks = run_sam_generator(gen, None, 256, lambda p, m: msgs.append(p),
                              make_cancel_check(None))
    assert len(masks) == 1
    assert sum(1 for e in log if isinstance(e, tuple)) == 16
    assert msgs[0] == 10 and max(msgs) == 50
    assert SAM_POINTS_PER_BATCH_CPU <= 16


# ----------------------------------------------------------------------
# Worker / queue: stops mid-image and between images, no partial result
# ----------------------------------------------------------------------

def test_analyze_image_honours_cancel(big_bgr):
    from ui.workers import analyze_image
    ev = threading.Event()
    ev.set()
    t0 = time.perf_counter()
    with pytest.raises(AnalysisCancelled):
        analyze_image(big_bgr, 1.0, DetectionParams(detection_mode="threshold"),
                      cancel=ev)
    assert time.perf_counter() - t0 < 0.2


def test_queue_cancel_mid_batch_is_prompt(qapp, big_bgr):
    from ui.workers import AnalysisJob, AnalysisQueue
    q = AnalysisQueue()
    finished, done, failed, started = [], [], [], []
    q.queue_finished.connect(finished.append)
    q.job_finished.connect(lambda uid, r: done.append(uid))
    q.job_failed.connect(lambda uid, m: failed.append(uid))
    q.job_progress.connect(lambda uid, p, m: started.append(uid))
    params = DetectionParams(detection_mode="boundary")
    jobs = [AnalysisJob(i, big_bgr, 1.0, params) for i in range(4)]
    assert q.start(jobs)
    deadline = time.perf_counter() + 15
    while not started and time.perf_counter() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert started
    t0 = time.perf_counter()
    q.cancel()
    while not finished and time.perf_counter() - t0 < 5:
        qapp.processEvents()
        time.sleep(0.005)
    latency = time.perf_counter() - t0
    assert finished == [True]
    assert latency < LATENCY_BUDGET_S, f"queue cancel took {latency:.2f}s"
    assert not q.is_running()
    assert failed == []
    assert done in ([], [0])      # the running image never yields a partial result
    assert 1 not in done and 2 not in done
