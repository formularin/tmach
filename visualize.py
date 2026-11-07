#!/usr/bin/env python3
"""Export a bounded reachable configuration graph to an interactive PyVis HTML file."""

from __future__ import annotations

import argparse
from collections import deque
from html import escape
import json
from pathlib import Path
import sys
import webbrowser

import tmach


def explore(machine, input_string, max_nodes=500, max_depth=50):
    """BFS with canonical configurations; retain cycles and all known-target edges.

    Frontier nodes have at least one successor omitted by an exploration limit.
    Edge descriptions belong to transitions, since several paths can reach a node.
    """
    initial = tmach.initial_config(machine, input_string)
    configs, depths = [initial], [0]
    ids = {tmach._config_key(initial): 0}
    queue = deque([0])
    edges, frontier = [], set()
    while queue:
        source = queue.popleft()
        config = configs[source]
        if config.state in tmach.HALTING:
            continue
        # successors() omits implicit rejection; step() implements that case.
        successors = list(tmach.successors(machine, config))
        if not successors:
            successors = [tmach.step(machine, config)]
        for nxt in successors:
            key = tmach._config_key(nxt)
            if key not in ids:
                if len(configs) >= max_nodes or depths[source] >= max_depth:
                    frontier.add(source)
                    continue
                ids[key] = len(configs)
                configs.append(nxt)
                depths.append(depths[source] + 1)
                queue.append(ids[key])
            description = nxt.prev or ""
            if nxt.notes:
                description += "\n" + "\n".join(nxt.notes)
            edges.append((source, ids[key], description))
    return configs, depths, edges, frontier


def configuration_text(config, index, depth, frontier):
    lines = [f"C{index} — state: {config.state}", f"Shortest distance from initial: {depth}"]
    if index == 0:
        lines.append("Initial configuration")
    if config.state in tmach.HALTING:
        lines.append("Accepted" if config.state == tmach.ACCEPT else "Rejected")
    if frontier:
        lines.append("PARTIAL: some successors omitted by exploration limits")
    for number, (tape, head) in enumerate(zip(config.tapes, config.heads), 1):
        last = max(head, max((i for i, s in enumerate(tape) if s != tmach.BLANK), default=0))
        cells = [tape[i] if i < len(tape) else tmach.BLANK for i in range(last + 2)]
        lines.extend(["", f"Tape {number} — head at cell {head} (cells start at 0)",
                      " ".join(f"[{s}]" if i == head else s for i, s in enumerate(cells))])
    lines.extend(["", "[symbol] = head position; _ = blank; start = cell 0.",
                  "All cells beyond those shown are blank."])
    return "\n".join(lines)


def write_graph(machine, input_string, output, max_nodes=500, max_depth=50):
    try:
        from pyvis.network import Network
    except ImportError:
        raise tmach.TMError("PyVis is required: python -m pip install -r requirements-visualize.txt") from None
    configs, depths, edges, frontier = explore(machine, input_string, max_nodes, max_depth)
    net = Network(height="70vh", width="100%", directed=True, cdn_resources="in_line")
    details = {}
    for i, config in enumerate(configs):
        details[str(i)] = configuration_text(config, i, depths[i], i in frontier)
        color = {tmach.ACCEPT: "#86efac", tmach.REJECT: "#fca5a5"}.get(config.state, "#93c5fd")
        if i in frontier:
            color = "#fcd34d"
        net.add_node(i, label=f"C{i}\n{config.state}", color=color,
                     shape="box", borderWidth=4 if i == 0 else 1,
                     title=escape(details[str(i)]).replace("\n", "<br>"))
    for i, (source, target, description) in enumerate(edges):
        net.add_edge(source, target, id=i, title=escape(description).replace("\n", "<br>"),
                     transition=description)
    net.set_options(json.dumps({
        "layout": {"randomSeed": 42},
        "physics": {"stabilization": {"iterations": 150}},
        "interaction": {"navigationButtons": True, "hover": True},
        "edges": {"smooth": {"type": "dynamic"}, "arrows": {"to": {"enabled": True}}},
    }))
    status = "Partial graph: yellow nodes have omitted successors." if frontier else "Complete reachable graph."
    choices = "".join(f'<option value="{i}">C{i} — {escape(c.state)}</option>' for i, c in enumerate(configs))
    panel = f"""
    <style>
    body {{font-family:system-ui,sans-serif; margin:16px; color:#172033}}
    #config-panel {{padding:16px; background:#f1f5f9; border-radius:8px}}
    #config-detail {{white-space:pre-wrap; overflow-wrap:anywhere}}
    button, select {{padding:6px; margin:4px}}
    </style>
    <section id="config-panel">
    <h2>Configuration graph</h2>
    <p>{escape(machine.path)} · input: <code>{escape(repr(input_string))}</code><br>
    {len(configs)} configurations · {len(edges)} transitions · {status}<br>
    Limits: {max_nodes} nodes, depth {max_depth}. Blue: running; green: accept; red: reject.
    Thick border: initial.</p>
    <p>Click a node to inspect its tapes. Click an arrow to inspect its transition.
    Drag to pan or move nodes; scroll to zoom.</p>
    <label>Find configuration <select id="config-select">{choices}</select></label>
    <button id="fit-graph">Fit graph</button>
    <button id="toggle-physics">Freeze layout</button>
    <pre id="config-detail"></pre>
    </section>
    """
    # Escape '<' so a spec symbol cannot terminate this script element.
    data = json.dumps(details).replace("<", "\\u003c")
    script = """
    <script>
    const configDetails = DATA;
    const detail = document.getElementById('config-detail');
    const picker = document.getElementById('config-select');
    function inspect(id) {
        picker.value = String(id);
        detail.textContent = configDetails[String(id)];
    }
    picker.addEventListener('change', () => {
        const id = Number(picker.value);
        network.selectNodes([id]);
        network.focus(id, {scale:1.2, animation:true});
        inspect(id);
    });
    network.on('click', params => {
        if (params.nodes.length) inspect(params.nodes[0]);
        else if (params.edges.length) {
            const edge = edges.get(params.edges[0]);
            detail.textContent = 'C' + edge.from + ' → C' + edge.to + '\\n' + edge.transition;
        }
    });
    document.getElementById('fit-graph').onclick = () => network.fit({animation:true});
    let moving = true;
    document.getElementById('toggle-physics').onclick = function () {
        moving = !moving;
        network.setOptions({physics:{enabled:moving}});
        this.textContent = moving ? 'Freeze layout' : 'Unfreeze layout';
    };
    inspect(0);
    </script>
    """.replace("DATA", data)
    html = net.generate_html(notebook=False)
    html = html.replace("<body>", "<body>" + panel).replace("</body>", script + "</body>")
    Path(output).write_text(html, encoding="utf-8")
    return len(configs), len(edges), bool(frontier)


def positive_int(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", help="machine specification")
    parser.add_argument("input", nargs="?", default="", help="input string (default: empty)")
    parser.add_argument("-o", "--output", default="configuration_graph.html")
    parser.add_argument("--max-nodes", type=positive_int, default=500)
    parser.add_argument("--max-depth", type=positive_int, default=50)
    parser.add_argument("--open", action="store_true", help="open the generated HTML in a browser")
    args = parser.parse_args(argv)
    try:
        machine = tmach.parse_spec(args.spec)
        for symbol in args.input:
            if symbol not in machine.alphabet:
                raise tmach.TMError(f"input must use alphabet {machine.alphabet!r}; found {symbol!r}")
        nodes, edges, partial = write_graph(machine, args.input, args.output, args.max_nodes, args.max_depth)
        print(f"Wrote {args.output}: {nodes} configurations, {edges} transitions" +
              (" (partial; increase --max-nodes or --max-depth to explore further)." if partial else " (complete)."))
        if args.open:
            webbrowser.open(Path(args.output).resolve().as_uri())
    except (tmach.TMError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
