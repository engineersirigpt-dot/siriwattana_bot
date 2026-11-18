"""Values a page injects with jQuery are read without running its script (offline)."""
from url_reader import _drop_dead_branches, _html_to_text

WI = """<html><head><meta charset="utf-8"><title>WI</title></head><body><form><span id="due1"></span></form>
<script>
\t$('#due1').html("30 ต.ค. 69");
\t$('#ok1').html("10 ต.ค. 69");
\tif ({flag} == 1) {{
\t\t$('body').append('<div id="cancelJobLogo" >Job Canceled</div>');
\t}}
\tvar data = "";
</script></body></html>"""


def test_a_job_that_is_not_cancelled_does_not_read_as_cancelled():
    # prod 2026-11-18: every WI page carries "if (0 == 1) { …Job Canceled… }" and the bot
    # told the user J52600860 was cancelled
    _, text = _html_to_text(WI.format(flag=0).encode(), "text/html")
    assert "due1: 30 ต.ค. 69" in text and "ok1: 10 ต.ค. 69" in text
    assert "Canceled" not in text


def test_a_cancelled_job_still_says_so():
    _, text = _html_to_text(WI.format(flag=1).encode(), "text/html")
    assert "body: Job Canceled" in text


def _flat(js: str) -> str:
    return " ".join(_drop_dead_branches(js).split())


def test_static_branches():
    assert _flat("if (0 == 1) { a(); } b();") == "b();"
    assert _flat("if (1 == 1) { a(); } b();") == "a(); b();"
    assert _flat("if ('x' != 'x') { a(); } else { c(); }") == "c();"
    assert _flat("if ('1' === 1) { a(); }") == "a();"                          # PHP-printed numbers
    # a condition on a variable is left alone — only literals are decided
    assert _drop_dead_branches("if (x == 1) { a(); }") == "if (x == 1) { a(); }"
