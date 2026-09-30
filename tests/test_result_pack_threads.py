"""core.result_pack - pack/unpack is reader-safe across threads (item 7)."""
import threading
import time

import numpy as np

from core.grain_detector import AnalysisResult
from core.result_pack import is_packed, pack_result, unpack_result, unpacked_copy


def _result():
    lab = np.zeros((120, 160), np.int32)
    lab[10:50, 10:60] = 1
    lab[60:110, 70:150] = 2
    return AnalysisResult(label_image=lab, binary_image=lab > 0,
                          valid_mask=np.ones(lab.shape, bool),
                          overlay_image=np.zeros(lab.shape + (3,), np.uint8))


def test_pack_publishes_before_dropping_arrays():
    r = _result()
    seen = []

    class Spy(type(r)):
        def __setattr__(self, name, value):
            if name == "label_image" and value is None:
                seen.append(is_packed(self))
            object.__setattr__(self, name, value)
    r.__class__ = Spy
    assert pack_result(r) and is_packed(r) and r.label_image is None
    assert seen == [True]


def test_unpack_restores_before_clearing_flag():
    r = _result()
    ref = r.label_image.copy()
    pack_result(r)
    seen = []

    class Spy(type(r)):
        def __setattr__(self, name, value):
            if name == "_packed_arrays" and value is None:
                seen.append(self.label_image is not None)
            object.__setattr__(self, name, value)
    r.__class__ = Spy
    assert unpack_result(r) and not is_packed(r)
    assert seen == [True] and np.array_equal(r.label_image, ref)


def test_concurrent_reader_never_sees_missing_labels():
    r = _result()
    ref = r.label_image.copy()
    stop = threading.Event()
    errors = []
    reads = [0]

    def reader():
        while not stop.is_set():
            try:
                snap = dict(r.__dict__)       # one consistent view (GIL)
                if snap.get("label_image") is None and snap.get("_packed_arrays") is None:
                    errors.append("label_image None on unpacked result")
                c = unpacked_copy(r)
                if c.label_image is None or not np.array_equal(c.label_image, ref):
                    errors.append("unpacked_copy lost labels")
                reads[0] += 1
            except Exception as exc:          # noqa: BLE001
                errors.append(repr(exc))

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    deadline = time.perf_counter() + 1.5
    n = 0
    while time.perf_counter() < deadline and not errors:
        pack_result(r)
        unpack_result(r)
        n += 1
    stop.set()
    t.join(5)
    assert not errors, errors[:3]
    assert n > 50 and reads[0] > 10
    assert np.array_equal(r.label_image, ref)
