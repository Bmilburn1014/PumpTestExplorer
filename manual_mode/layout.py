# manual_mode/layout.py

from dash import dcc, html

_P = "mm"

def manual_id(name: str) -> str:
    return f"{_P}-{name}"

def _date_range(panel):
    return html.Div(className="row gap-8", children=[
        html.Div(className="col", children=[
            html.Label("From:", className="field-label"),
            dcc.Input(id=manual_id(f"{panel}-date-start"),
                      type="date", className="date-input"),
        ]),
        html.Div(className="col", children=[
            html.Label("To:", className="field-label"),
            dcc.Input(id=manual_id(f"{panel}-date-end"),
                      type="date", className="date-input"),
        ]),
    ])

def _baseline_panel():
    return html.Div(
        className="section",
        style={"borderLeft": "3px solid #1a73e8",
               "paddingLeft": "10px", "marginBottom": "10px"},
        children=[
            html.Div("Baseline (curve-fitted reference)",
                     className="section-header",
                     style={"color": "#1a73e8", "fontSize": "12px"}),
            _date_range("bl"),
            html.Div("Leave blank for all dates", className="hint-text"),
            html.Div(style={"marginBottom": "6px"}, children=[
                html.Label("Model", className="field-label"),
                dcc.Dropdown(id=manual_id("bl-model"),
                             placeholder="Select pump type first…",
                             clearable=True, disabled=True),
            ]),
            html.Div(id=manual_id("part-num-row"),
                     style={"display": "none", "marginBottom": "6px"},
                     children=[
                html.Label("Impeller Part Number", className="field-label"),
                dcc.Input(id=manual_id("bl-part-num"), type="text",
                          placeholder="e.g. 504291814",
                          className="date-input",
                          style={"width": "100%"}, debounce=True),
            ]),
            html.Div(className="row gap-8", children=[
                html.Div(className="col", style={"flex": "1"}, children=[
                    html.Label("Impeller Trim (in)", className="field-label"),
                    dcc.Input(id=manual_id("bl-trim"), type="number",
                              placeholder="e.g. 7.125", step="any", min=0,
                              className="date-input", style={"width": "100%"}),
                ]),
                html.Div(className="col", style={"flex": "1"}, children=[
                    html.Label("Speed (RPM)", className="field-label"),
                    dcc.Input(id=manual_id("bl-speed"), type="number",
                              placeholder="e.g. 1780", step="any", min=0,
                              className="date-input", style={"width": "100%"}),
                ]),
            ]),
        ])

def _raw_panel():
    return html.Div(
        className="section",
        style={"borderLeft": "3px solid #d93025",
               "paddingLeft": "10px", "marginBottom": "10px"},
        children=[
            html.Div("Raw / Comparison data", className="section-header",
                     style={"color": "#d93025", "fontSize": "12px"}),
            _date_range("raw"),
            html.Div("Leave blank for all dates", className="hint-text"),
            html.Div("Same model as baseline — tests filtered by "
                     "trim grouping tolerance below",
                     className="hint-text", style={"fontStyle": "italic"}),
        ])

def build_manual_panel():
    toggle = html.Div(
        className="row gap-8 align-center", style={"marginBottom": "6px"},
        children=[
            dcc.Checklist(id=manual_id("toggle"),
                          options=[{"label": " Manual Mode", "value": "on"}],
                          value=[], className="fit-check",
                          style={"fontWeight": "600"}),
            html.Span("Select tests directly — no PX file needed",
                      className="hint-text", style={"margin": "0"}),
        ])
    panel = html.Div(
        id=manual_id("panel"), style={"display": "none"},
        children=[
            html.Div(className="row gap-8 align-center",
                     style={"marginBottom": "4px"}, children=[
                html.Label("Pump Type", className="field-label",
                           style={"marginBottom": "0"}),
                dcc.Dropdown(id=manual_id("pump-type"),
                    options=[
                        {"label": "Inline (PV / PVF)", "value": "inline"},
                        {"label": "Horizontal (AE / TU)", "value": "horizontal"},
                        {"label": "End Suction (F / C)", "value": "end_suction"},
                        {"label": "Vertical Turbine", "value": "vertical"},
                    ],
                    placeholder="Select pump type…",
                    clearable=False, style={"flex": "1"}),
            ]),
            html.Div(id=manual_id("index-status"),
                     style={"display": "none"}, className="status-text"),
            dcc.Store(id=manual_id("index-ready"), data=None),
            _baseline_panel(),
            _raw_panel(),
            html.Div(className="slider-row", children=[
                html.Div(className="row space-between", children=[
                    html.Label("Trim Grouping Tolerance:",
                               className="field-label"),
                    html.Span(id=manual_id("trim-tol-readout"),
                              className="accent-value"),
                ]),
                dcc.Slider(id=manual_id("trim-tolerance"),
                    min=0, max=5, step=0.5, value=3,
                    marks={0:"0%",1:"1%",2:"2%",3:"3%",4:"4%",5:"5%"},
                    tooltip={"placement":"bottom","always_visible":False}),
            ]),
            html.Button("Run Manual Compare", id=manual_id("compare-btn"),
                        n_clicks=0, className="btn-run",
                        style={"marginTop": "8px"}),
            html.Div(id=manual_id("status"), className="status-text"),
        ])
    return html.Div(id=manual_id("container"),
        style={"borderTop": "1px solid #e5e7eb",
               "paddingTop": "8px", "marginTop": "8px"},
        children=[toggle, panel])
