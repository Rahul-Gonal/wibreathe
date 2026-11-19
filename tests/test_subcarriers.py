"""Unit tests for subcarrier selection and data-driven null detection."""

import math
from wibreathe.dsp.subcarriers import SubcarrierSelector


def test_empirical_null_subcarrier_detection():
    """Verify that dead/null subcarriers with near-zero amplitude are detected dynamically."""
    # Create 50 time steps x 64 subcarriers
    # Subcarriers 0, 1, 32, 63 have zero amplitude; others have normal signal
    matrix = []
    for t in range(50):
        row = [10.0 + math.sin(t * 0.1 + k) for k in range(64)]
        row[0] = 0.0
        row[1] = 0.0001
        row[32] = 0.0
        row[63] = 0.0002
        matrix.append(row)

    detected_nulls = SubcarrierSelector.detect_null_subcarriers(matrix, threshold_ratio=0.05)
    assert 0 in detected_nulls
    assert 1 in detected_nulls
    assert 32 in detected_nulls
    assert 63 in detected_nulls
    # Normal subcarrier (e.g. index 15) must not be flagged
    assert 15 not in detected_nulls


def test_subcarrier_ranking_and_selection():
    """Verify that subcarriers with highest respiration modulation variance are ranked top."""
    selector = SubcarrierSelector(raw_subcarrier_count=64, top_k=3, method="variance")
    matrix = []
    for t in range(50):
        row = [5.0 for _ in range(64)]
        # Subcarrier 20 has large sinusoidal modulation
        row[20] = 5.0 + 3.0 * math.sin(t * 0.2)
        # Subcarrier 25 has moderate modulation
        row[25] = 5.0 + 1.5 * math.sin(t * 0.2)
        # Subcarrier 30 has tiny modulation
        row[30] = 5.0 + 0.5 * math.sin(t * 0.2)
        matrix.append(row)

    top3 = selector.update_selection(matrix)
    assert len(top3) == 3
    assert top3[0] == 20  # Highest variance
    assert top3[1] == 25  # Second highest
    assert top3[2] == 30  # Third highest


def test_combine_channels():
    """Verify combining multiple subcarriers into a 1D timeseries."""
    selector = SubcarrierSelector(raw_subcarrier_count=64, top_k=2)
    matrix = [[1.0, 2.0] * 32 for _ in range(20)]
    combined = selector.combine_channels(matrix, selected_indices=[10, 11])
    assert len(combined) == 20


if __name__ == "__main__":
    test_empirical_null_subcarrier_detection()
    test_subcarrier_ranking_and_selection()
    test_combine_channels()
    print("Subcarrier tests passed.")
