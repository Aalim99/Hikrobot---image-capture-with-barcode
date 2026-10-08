import numpy as np
import pytest

from frame import BAYER_TO_BGR, Frame
from helpers import BAYER_BLOCKS, bayer_frame

# stripes of pure red / green / blue (BGR order)
RED, GREEN, BLUE = (20, 20, 220), (20, 220, 20), (220, 20, 20)


def _stripes(h=64, w=96):
    img = np.zeros((h, w, 3), np.uint8)
    img[:, :32] = RED
    img[:, 32:64] = GREEN
    img[:, 64:] = BLUE
    return img


@pytest.mark.parametrize("pixel_format", sorted(BAYER_BLOCKS))
def test_every_bayer_pattern_debayers_to_the_right_colours(pixel_format):
    source = _stripes()
    out = bayer_frame(source, pixel_format).bgr()

    assert out.shape == source.shape
    # compare away from the stripe borders, where demosaicing interpolates
    for cols in (slice(6, 26), slice(38, 58), slice(70, 90)):
        got = out[8:-8, cols].reshape(-1, 3).mean(axis=0)
        want = source[8:-8, cols].reshape(-1, 3).mean(axis=0)
        assert np.abs(got - want).max() < 12, f"{pixel_format}: {got} != {want}"


def test_all_supported_bayer_formats_are_mapped():
    assert set(BAYER_TO_BGR) == set(BAYER_BLOCKS)


def test_crop_at_odd_offset_keeps_colour_phase_and_reports_real_rect():
    source = _stripes()
    frame = bayer_frame(source, "BayerGB8")

    crop, rect = frame.crop_bgr((33, 11, 20, 30))  # odd left/top
    left, top, w, h = rect
    assert left % 2 == 0 and top % 2 == 0 and w % 2 == 0 and h % 2 == 0
    assert left <= 33 and top <= 11 and left + w >= 53 and top + h >= 41
    assert crop.shape[:2] == (h, w)

    # green stripe (cols 32..63) must still be green inside the crop
    inner = crop[4:-4, 4:-4].reshape(-1, 3).mean(axis=0)
    assert np.abs(inner - np.array(GREEN)).max() < 12


def test_crop_is_clipped_to_the_frame_and_empty_crop_is_none():
    frame = bayer_frame(_stripes(), "BayerRG8")
    crop, rect = frame.crop_bgr((80, 50, 500, 500))
    assert rect[0] + rect[2] <= 96 and rect[1] + rect[3] <= 64
    assert crop.shape[:2] == (rect[3], rect[2])

    crop, rect = frame.crop_bgr((200, 200, 10, 10))
    assert crop is None and rect == (0, 0, 0, 0)


def test_preview_downscales_and_reports_scale():
    frame = bayer_frame(np.zeros((400, 600, 3), np.uint8), "BayerRG8")
    image, scale = frame.preview(max_dim=300)
    assert scale == pytest.approx(0.5)
    assert image.shape == (200, 300, 3)

    image, scale = frame.preview(max_dim=5000)  # never upscales
    assert scale == 1.0 and image.shape == (400, 600, 3)


def test_bgr_and_mono_frames_pass_through():
    bgr = _stripes()
    assert Frame(bgr, "BGR8").bgr() is bgr
    mono = Frame(np.full((10, 12), 7, np.uint8), "Mono8")
    assert mono.bgr().shape == (10, 12, 3)
    assert (mono.width, mono.height) == (12, 10)


def test_unknown_pixel_format_is_rejected():
    with pytest.raises(ValueError):
        Frame(np.zeros((4, 4), np.uint8), "YUV422")
