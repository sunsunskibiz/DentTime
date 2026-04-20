from src.features.feature_transformer import build_treatment_encoding

SAMPLE_DICT = {
    "SCALING": ["ขูดหินปูน"],
    "EXTRACTION": ["ถอนฟัน"],
    "COMPOSITE_FILL": ["อุดฟัน"],
    "UNKNOWN": [],
}


def test_encoding_is_deterministic():
    enc1 = build_treatment_encoding(SAMPLE_DICT)
    enc2 = build_treatment_encoding(SAMPLE_DICT)
    assert enc1 == enc2


def test_all_classes_present():
    enc = build_treatment_encoding(SAMPLE_DICT)
    assert set(enc.keys()) == set(SAMPLE_DICT.keys())


def test_values_are_contiguous_from_zero():
    enc = build_treatment_encoding(SAMPLE_DICT)
    assert sorted(enc.values()) == list(range(len(SAMPLE_DICT)))


def test_values_are_unique():
    enc = build_treatment_encoding(SAMPLE_DICT)
    assert len(set(enc.values())) == len(enc)


def test_unknown_is_present_and_is_int():
    enc = build_treatment_encoding(SAMPLE_DICT)
    assert "UNKNOWN" in enc
    assert isinstance(enc["UNKNOWN"], int)


def test_alphabetical_ordering():
    enc = build_treatment_encoding(SAMPLE_DICT)
    # sorted keys: COMPOSITE_FILL→0, EXTRACTION→1, SCALING→2, UNKNOWN→3
    assert enc["COMPOSITE_FILL"] == 0
    assert enc["EXTRACTION"] == 1
    assert enc["SCALING"] == 2
    assert enc["UNKNOWN"] == 3
