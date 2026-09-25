"""Turn measured demo results into a readable, self-contained SVG chart."""

import argparse
import json
from html import escape
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COLORS = {"p50": "#1677b5", "p95": "#eb7135"}


def render(results):
    phases = results["phases"]
    labels = {"steady": "Normal", "failure": "Fast worker fails", "recovery": "Recovered"}
    values = [phases[name]["latency_ms"][metric] for name in labels for metric in ("p50", "p95")]
    scale_max = max(10, int((max(values) + 9) // 10 * 10))
    chart_left, chart_top, chart_width, chart_height = 170, 168, 690, 280
    items = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 960 560" role="img" aria-labelledby="title desc">',
        '<title id="title">Measured gateway latency across normal, failure, and recovery phases</title>',
        '<desc id="desc">Locally measured p50 and p95 HTTP latency for simulated inference workers. Every phase completed 40 of 40 requests successfully.</desc>',
        '<rect width="960" height="560" fill="#ffffff"/>',
        '<text x="48" y="55" font-family="Arial,sans-serif" font-size="30" font-weight="700" fill="#12243a">Inference gateway: failure and recovery</text>',
        '<text x="48" y="88" font-family="Arial,sans-serif" font-size="16" fill="#51647a">Measured loopback HTTP demo · simulated workers · 40 requests per phase</text>',
        '<rect x="48" y="107" width="864" height="397" rx="16" fill="#f7f9fc" stroke="#dce5ef"/>',
    ]
    for tick in range(0, 5):
        x = chart_left + chart_width * tick / 4
        value = scale_max * tick / 4
        items.append('<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" stroke="#d9e2eb"/>' % (
            x, chart_top - 8, x, chart_top + chart_height))
        items.append('<text x="%.1f" y="%d" text-anchor="middle" font-family="Arial,sans-serif" font-size="13" fill="#51647a">%.0f</text>' % (
            x, chart_top + chart_height + 27, value))
    items.append('<text x="%d" y="%d" text-anchor="middle" font-family="Arial,sans-serif" font-size="14" fill="#51647a">End-to-end latency (ms)</text>' % (
        chart_left + chart_width // 2, chart_top + chart_height + 55))
    for row, (name, label) in enumerate(labels.items()):
        base_y = chart_top + 15 + row * 86
        items.append('<text x="70" y="%d" font-family="Arial,sans-serif" font-size="16" font-weight="700" fill="#12243a">%s</text>' % (
            base_y + 27, escape(label)))
        for offset, metric in ((0, "p50"), (31, "p95")):
            value = phases[name]["latency_ms"][metric]
            width = chart_width * value / scale_max
            y = base_y + offset
            items.append('<rect x="%d" y="%d" width="%.1f" height="22" rx="4" fill="%s"/>' % (
                chart_left, y, width, COLORS[metric]))
            items.append('<text x="%.1f" y="%d" font-family="Arial,sans-serif" font-size="13" fill="#12243a">%.2f ms</text>' % (
                chart_left + width + 8, y + 16, value))
    items += [
        '<rect x="675" y="120" width="16" height="16" rx="3" fill="%s"/>' % COLORS["p50"],
        '<text x="698" y="134" font-family="Arial,sans-serif" font-size="14" fill="#334b63">p50</text>',
        '<rect x="762" y="120" width="16" height="16" rx="3" fill="%s"/>' % COLORS["p95"],
        '<text x="785" y="134" font-family="Arial,sans-serif" font-size="14" fill="#334b63">p95</text>',
        '<text x="48" y="534" font-family="Arial,sans-serif" font-size="13" fill="#51647a">Failure was injected as HTTP 503 on the fast worker; the gateway retried on the replica.</text>',
        '</svg>',
    ]
    return "\n".join(items) + "\n"


def main():
    parser = argparse.ArgumentParser(description="Render the local demo chart")
    parser.add_argument("--input", default=str(ROOT / "docs" / "demo-results.json"))
    parser.add_argument("--output", default=str(ROOT / "docs" / "images" / "demo-latency.svg"))
    args = parser.parse_args()
    results = json.loads(Path(args.input).read_text(encoding="utf-8"))
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(render(results), encoding="utf-8")
    print("Chart: %s" % destination)


if __name__ == "__main__":
    main()
