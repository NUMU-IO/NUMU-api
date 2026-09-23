"""An oversized optional field must never reject a whole /track event.

Real TikTok click ids run past 256 characters. The old cap rejected every
browser event from a TikTok-ad visitor with a 422 that the storefront proxy
hid behind a 204.
"""

from src.api.v1.routes.storefront.tracking import TrackPageViewRequest


def test_real_length_ttclid_is_kept_whole():
    ttclid = "E.C.P." + "a" * 400
    body = TrackPageViewRequest(path="/", ttclid=ttclid, fbc="fb.1.1." + "b" * 300)

    assert body.ttclid == ttclid
    assert body.fbc == "fb.1.1." + "b" * 300


def test_absurd_identifiers_are_dropped_not_rejected():
    body = TrackPageViewRequest(
        path="/", ttclid="x" * 5000, fbc="y" * 5000, fbp="z" * 500, ttp="t" * 500
    )

    assert (body.ttclid, body.fbc, body.fbp, body.ttp) == (None, None, None, None)


def test_long_descriptive_fields_are_truncated():
    body = TrackPageViewRequest(
        path="/", referrer="r" * 900, page_url="https://s.test/" + "p" * 3000
    )

    assert len(body.referrer) == 500
    assert len(body.page_url) == 2000
