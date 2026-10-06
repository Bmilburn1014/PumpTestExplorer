# layout/main_layout.py
#
# Layout:
#   Left push-drawer (setup — auto-closes after data loads)
#   Center charts (flex: 1, expands when left drawer closes)
#   Right overlay drawers (trim groups, analysis)
#
# Shape tools are collapsible sections inside each trim card —
# no separate drawer needed.

from dash import html, dcc
from manual_mode.layout import build_manual_panel

GROUP_COLORS = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
    "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
]
VIRTUAL_COLORS = [
    "#e91e63", "#00bcd4", "#ff9800", "#4caf50", "#9c27b0",
]
VIRTUAL_INDEX_OFFSET = 100
MAX_KNOTS = 25


def build_layout():
    return html.Div(
        className="app-container",
        children=[
            # -- Stores -------------------------------------------
            dcc.Store(id="px-data", data=None),
            dcc.Store(id="comparison-results", data=None),
            dcc.Store(id="group-config", data={}),
            dcc.Store(id="fit-settings", storage_type = "session", data={}),
            dcc.Store(id="test-visibility", data={}, storage_type="session"),
            dcc.Store(id="shape-settings", data={"head":{}, "power":{}, "active": "head"}, storage_type="session"),
            dcc.Store(id="added-trims", data=[]),
            dcc.Store(id="group-visibility", data={}, storage_type="session"),

            # -- Export helpers ------------------------------------
            dcc.Interval(id="cache-refresh-interval",
                         interval=30_000, n_intervals=0),

            # -- Top bar ------------------------------------------
            _top_bar(),

            # -- Main flex layout ---------------------------------
            html.Div(
                className="main-content",
                children=[
                    # Left push-drawer (in flex flow)
                    html.Div(
                        id="left-drawer",
                        className="left-drawer open",
                        children=[_left_panel_content()],
                    ),
                    # Center charts
                    _center_panel(),
                    # Right push-drawer: Trim Groups
                    html.Div(
                        id="right-drawer",
                        className="right-drawer",
                        children=[_right_panel()],
                    ),
                    # Right push-drawer: Trim Analysis
                    html.Div(
                        id="analysis-drawer",
                        className="right-drawer analysis",
                        children=[_analysis_panel()],
                    ),
                    html.Div(
                        id="ml-drawer",
                        className="right-drawer ml",
                        children=[_ml_panel()],
                    ),
                ],
            ),

            # -- Status bar ---------------------------------------
            html.Div(
                id="status-bar", className="status-bar",
                children=[
                    html.Div(id="progress-bar-wrap",
                             className="progress-bar-wrap",
                             children=[
                                 html.Div(id="progress-bar-fill",
                                          className="progress-bar-fill",
                                          style={"width": "0%"}),
                             ]),
                    html.Span("", id="progress-label",
                              className="progress-label"),
                    html.Span("Ready", id="status-line-1",
                              className="status-line"),
                    html.Span("", id="status-line-2",
                              className="status-line dim"),
                ],
            ),
            dcc.Interval(id="status-interval",
                         interval=500, n_intervals=0),
        ],
    )


# =====================================================================
# TOP BAR
# =====================================================================

def _top_bar():
    return html.Div(
        className="top-bar",
        children=[
            html.Div(className="top-bar-left", children=[
                html.Button(
                    "☰ Setup",
                    id="toggle-left-drawer-btn",
                    n_clicks=0,
                    className="btn-drawer-toggle",
                ),
                html.Span("Pump Test Data Explorer",
                          className="app-title"),
                html.Span("—", className="title-sep"),
                html.Span("PX Curve Comparison",
                          className="app-subtitle"),
            ]),
            html.Div(className="top-bar-right", children=[
                html.Span("Head units:", className="unit-label"),
                dcc.RadioItems(
                    id="unit-toggle",
                    options=[
                        {"label": " ft", "value": "feet"},
                        {"label": " PSI", "value": "psi"},
                    ],
                    value="feet", inline=True,
                    className="unit-radios",
                ),
                html.Button(
                    "☰ Machine Learning Tools",
                    id="toggle-ml-drawer-btn",
                    n_clicks=0,
                    className="btn-drawer-toggle btn-ml",
                ),
                html.Button(
                    "☰ Trim Analysis",
                    id="toggle-analysis-drawer-btn",
                    n_clicks=0,
                    className="btn-drawer-toggle btn-analysis",
                ),
                html.Button(
                    "☰ Trim Groups",
                    id="toggle-drawer-btn",
                    n_clicks=0,
                    className="btn-drawer-toggle",
                ),
            ]),
        ],
    )


# =====================================================================
# LEFT PANEL CONTENT (inside the push drawer)
# =====================================================================

def _left_panel_content():
    return html.Div(
        className="left-panel-inner",
        children=[
            # -- Auto-mode controls (dimmed when manual mode is on) --
            html.Div(
                id="auto-mode-controls",
                children=[
                    _section("1. Load PX Base Curves", [
                        html.Div(className="row gap-8 align-center", children=[
                            dcc.Input(
                                id="px-file-path", type="text",
                                placeholder="Path to PX curves .xlsx …",
                                className="full-input", debounce=True,
                                style={"flex": "1", "marginBottom": "0"},
                            ),
                            html.Button("Browse…", id="browse-px-btn",
                                        n_clicks=0, className="btn-browse"),
                        ]),
                        html.Button("Load PX File", id="load-px-btn",
                                    n_clicks=0, className="btn-primary"),
                        dcc.Loading(
                            id="px-loading", type="circle", color="#1a73e8",
                            children=html.Div(id="px-status",
                                              className="status-text"),
                        ),
                    ]),
                    html.Div(id="px-info-panel",
                             className="info-banner hidden"),
                    _section("2. Search Parameters", [
                        html.Div(className="row gap-8", children=[
                            html.Div(className="col", children=[
                                html.Label("From:", className="field-label"),
                                dcc.Input(id="date-start", type="date",
                                          className="date-input"),
                            ]),
                            html.Div(className="col", children=[
                                html.Label("To:", className="field-label"),
                                dcc.Input(id="date-end", type="date",
                                          className="date-input"),
                            ]),
                        ]),
                        html.Div("Leave blank for all dates",
                                 className="hint-text"),
                        html.Div(className="slider-row", children=[
                            html.Div(className="row space-between", children=[
                                html.Label("Trim Grouping Tolerance:",
                                           className="field-label"),
                                html.Span(id="trim-tol-readout",
                                          className="accent-value"),
                            ]),
                            dcc.Slider(
                                id="trim-tolerance", min=0, max=5, step=0.5,
                                value=3,
                                marks={0: "0%", 1: "1%", 2: "2%",
                                       3: "3%", 4: "4%", 5: "5%"},
                                tooltip={"placement": "bottom",
                                         "always_visible": False},
                            ),
                            html.Div(className="row space-between", children=[
                                html.Label("Test Iteration Select:",
                                           className="field-label"),
                                html.Span(id="test-iter-readout",
                                          className="accent-value"),
                            ]),
                            dcc.Slider(
                                id="test-iteration", min=1, max=3, step=1,
                                value=3,
                                marks={1: "1", 2: "2", 3: "All"},
                                tooltip={"placement": "bottom",
                                         "always_visible": False},
                            ),
                        ]),
                        html.Div(
                            id="vertical-impeller-filter-wrap",
                            className="vertical-impeller-filter hidden",
                            children=[
                                html.Label("Impeller Part Numbers:", className="field-label"),
                                dcc.Dropdown(
                                    id="vertical-impeller-parts",
                                    options=[],
                                    value=[],
                                    multi=True,
                                    clearable=True,
                                    searchable=True,
                                    placeholder="Select one or more impeller part numbers...",
                                    disabled=True,
                                    persistence=True,
                                    persistence_type="session",
                                ),
                                html.Div(
                                    id="vertical-impeller-parts-help",
                                    className="hint-text",
                                    children="Only shown for vertical pump comparisons.",
                                ),
                            ],
                        ),
                
                        # Head and power sources auto-detected
                        dcc.Input(id="head-source", type="hidden",
                                  value="tdh_bowl"),
                        dcc.Input(id="power-source", type="hidden",
                                  value="power_bowl_dyno"),
                    ]),
                    html.Button("Run Comparison", id="run-btn", n_clicks=0,
                                disabled=True, className="btn-run"),
                    html.Div(id="run-status", className="status-text"),
                ],
            ),
            # -- Manual mode panel (toggle + cascading selectors) --
            build_manual_panel(),

            html.Div(id="results-summary",
                     className="summary-card hidden"),

            # -- Export / Session section --------------------------
            _section("3. Export & Session", [
                html.Button("Export PSD Curves", id="export-psd-btn",
                            n_clicks=0, className="btn-export"),
                html.Div(id="export-psd-status",
                         className="status-text"),
                html.Hr(className="section-divider"),
                html.Div(className="row gap-8", children=[
                    html.Button("Save Session", id="save-session-btn",
                                n_clicks=0, className="btn-session"),
                    html.Button("Load Session", id="load-session-btn",
                                n_clicks=0, className="btn-session"),
                ]),
                html.Div(id="session-status",
                         className="status-text"),
                html.Hr(className="section-divider"),
                html.Div(className="cache-bar-section", children=[
                    html.Div(className="row space-between align-center",
                             children=[
                        html.Span("Local Cache",
                                  className="cache-bar-title"),
                        html.Span(id="cache-size-label",
                                  className="cache-size-text"),
                    ]),
                    html.Div(className="cache-bar-track", children=[
                        html.Div(id="cache-bar-fill",
                                 className="cache-bar-fill",
                                 style={"width": "0%"}),
                    ]),
                    html.Div(className="row space-between align-center",
                             children=[
                        html.Span("0 / 500 MB",
                                  id="cache-bar-numbers",
                                  className="cache-bar-numbers"),
                        html.Button("Clear Cache",
                                    id="clear-cache-btn",
                                    n_clicks=0,
                                    className="btn-cache-clear"),
                    ]),
                ]),
                html.Div(id="cache-clear-status",
                         className="status-text"),
                html.Hr(className="section-divider"),
                html.Div(className="row gap-8 align-center", children=[
                    html.Button("Open Logs Folder",
                                id="open-logs-btn",
                                n_clicks=0,
                                className="btn-logs"),
                    html.Span(id="logs-status",
                              className="status-text"),
                ]),
            ]),
        ],
    )


# =====================================================================
# CENTER PANEL — Charts
# =====================================================================

def _center_panel():
    gcfg = {
        "scrollZoom": True,
        "displayModeBar": True,
        "modeBarButtonsToAdd": [
            "drawline", "drawrect", "eraseshape",
        ],
    }
    return html.Div(
        className="center-panel",
        children=[
            html.Div(id="crosshair-readout",
                     className="crosshair-bar",
                     children="Hover over chart for readout"),
            dcc.Tabs(
                id="chart-tabs", value="head",
                className="chart-tabs",
                children=[
                    dcc.Tab(label="Head vs Flow", value="head",
                            className="chart-tab",
                            selected_className="chart-tab-sel"),
                    dcc.Tab(label="Power vs Flow", value="power",
                            className="chart-tab",
                            selected_className="chart-tab-sel"),
                    dcc.Tab(label="Efficiency vs Flow",
                            value="efficiency",
                            className="chart-tab",
                            selected_className="chart-tab-sel"),
                    dcc.Tab(label="Combined", value="combined",
                            className="chart-tab",
                            selected_className="chart-tab-sel"),
                ],
            ),
            html.Div(id="single-chart-wrap", className="chart-area",
                     children=[
                         dcc.Graph(id="main-chart", config=gcfg,
                                   style={"height": "100%",
                                          "width": "100%"},
                                   responsive=True),
                     ]),
            html.Div(id="combined-chart-wrap",
                     className="chart-area hidden",
                     children=[
                         dcc.Graph(id="combined-chart",
                                   config=gcfg,
                                   style={"height": "100%",
                                          "width": "100%"},
                                   responsive=True),
                     ]),
        ],
    )


# =====================================================================
# RIGHT PANEL — Trim Groups (overlay drawer)
# =====================================================================

def _right_panel():
    return html.Div(
        className="right-panel",
        children=[
            html.Div(className="row space-between align-center mb-8",
                     children=[
                         html.Span("Trim Groups",
                                   className="panel-title"),
                         html.Button("Show All", id="show-all-btn",
                                     n_clicks=0, className="btn-small"),
                     ]),
            html.Div(className="baseline-toggle-card", children=[
                dcc.Checklist(
                    id="show-baseline",
                    options=[{"label": " Show PX Baseline Curves",
                              "value": "on"}],
                    value=["on"], className="fw-bold f12",
                ),
                html.Div("Solid lines — one per trim diameter in PX file",
                         className="hint-text"),
            ]),
            html.Div(className="band-card", children=[
                html.Div(className="row space-between", children=[
                    html.Label("Acceptance Band:",
                               className="field-label"),
                    html.Span(id="band-readout",
                              className="accent-value"),
                ]),
                dcc.Slider(
                    id="band-pct", min=0, max=15, step=0.5, value=6,
                    marks={0: "0%", 3: "3%", 6: "6%",
                           10: "10%", 15: "15%"},
                    tooltip={"placement": "bottom",
                             "always_visible": False},
                ),
            ]),
            html.Div(
                id="trim-group-panels",
                children=[
                    html.P("Load a PX file and run comparison "
                           "to see results.",
                           className="empty-message"),
                ],
            ),
            html.Div(id="virtual-group-panels"),
        ],
    )

#======================================================================
# BUILD RIGHT PANEL DRAWERS - ANALYSIS AND ML
#======================================================================
def _analysis_panel():
    return build_drawer(
        "Trim Analysis",
        "Analyze test trim distribution and identify optimal PX baseline curves.",
        "analysis-panels",
        "analysis-panel",
    )

def _ml_panel():
    return build_drawer(
        "Machine Learning Tools",
        "Analyze current test data to predict failure modes and drift from PX baseline curves.",
        "ml-panels",
        "ml-panel",
    )
# =====================================================================
# TRIM GROUP CARD BUILDER — includes collapsible shape tools
# =====================================================================

def build_trim_group_card(idx, grp, chart_type, fit_settings=None,
                          shape_settings=None, group_visibility=None,
                          test_visibility=None):
    """Build one trim-group card with integrated shape tools."""
    color = GROUP_COLORS[idx % len(GROUP_COLORS)]
    nom = grp["nominal_diameter"]
    aff = grp.get("affinity_applied", False)
    tests = grp.get("tests", [])

    n_total = len(tests)
    n_data = sum(1 for t in tests if t.get("has_data"))
    header = f'PX {nom}" Trim  ({n_data}/{n_total} tests)'

    if aff:
        sub = f'Avg actual trim → scaled ×{grp["diameter_ratio"]:.3f}'
    else:
        sub = f'Tests within tolerance of {nom}"'

    # Resolve initial group-vis from saved settings
    gv = group_visibility or {}
    grp_checked = gv.get(str(idx), True)
    fs = (fit_settings or {}).get(chart_type, {}).get(str(idx), {})
    ss = (shape_settings or {}).get(chart_type, {}).get(str(idx), {})


    children = [
        html.Div(className="group-header", children=[
            dcc.Checklist(
                id={"type": "group-vis", "index": idx},
                options=[{"label": "", "value": "on"}],
                value=["on"] if grp_checked else [],
                className="mini-check",
            ),
            html.Div(className="color-swatch",
                     style={"backgroundColor": color}),
            html.Span(header, className="group-title"),
        ]),
        html.Div(sub, className="group-sub"),
    ]

    # -- Fit Controls -------------------------------------------------
    children.append(_build_fit_controls(idx, chart_type, fs))

    # -- Outlier badge + auto-clean -----------------------------------
    children.append(html.Div(
        className="outlier-row", children=[
            html.Div(id={"type": "outlier-badge", "index": idx},
                     className="outlier-badge-placeholder"),
            html.Button("Auto Clean",
                        id={"type": "auto-clean-btn", "index": idx},
                        n_clicks=0, className="btn-auto-clean"),]
    ))

    # -- Shape Tools (collapsible) ------------------------------------
    children.append(_build_shape_section(idx, chart_type, ss))

    # -- Test rows (with dates) ---------------------------------------
    children.append(_build_test_list(idx, tests, test_visibility))

    return html.Div(className="group-card",
                    style={"borderColor": color},
                    children=children)


# =====================================================================
# VIRTUAL TRIM GROUP CARD BUILDER
# =====================================================================

def build_virtual_trim_card(vi, v_info, tests, chart_type=None, fit_settings=None,
                            shape_settings=None, group_visibility=None,
                            test_visibility=None):
    """Build a card for a virtual (user-added) trim group."""
    idx = VIRTUAL_INDEX_OFFSET + vi
    color = VIRTUAL_COLORS[vi % len(VIRTUAL_COLORS)]
    v_dia = v_info["diameter"]
    px_dia = v_info["nearest_px"]

    n_total = len(tests)
    n_data = sum(1 for t in tests if t.get("has_data"))
    header = f'Added {v_dia:.3f}" Trim  ({n_data}/{n_total} tests)'
    sub = f'Affinity-scaled from PX {px_dia:.3f}" baseline'

    gv = group_visibility or {}
    grp_checked = gv.get(str(idx), True)
    chart_fit_settings = (fit_settings or {}).get(str(chart_type), {})
    fs = chart_fit_settings.get(str(idx), {})
    ss = (shape_settings or {}).get(str(idx), {})

    children = [
        html.Div(className="group-header", children=[
            dcc.Checklist(
                id={"type": "group-vis", "index": idx},
                options=[{"label": "", "value": "on"}],
                value=["on"] if grp_checked else [],
                className="mini-check",
            ),
            html.Div(className="color-swatch",
                     style={"backgroundColor": color}),
            html.Span(header, className="group-title"),
        ]),
        html.Div(sub, className="group-sub"),
        html.Div("Virtual — added from Trim Analysis",
                 className="group-sub",
                 style={"fontStyle": "italic", "color": "#0d7377"}),
    ]

    # -- Fit Controls -------------------------------------------------
    children.append(_build_fit_controls(idx, chart_type, fs))

    # -- Outlier badge + auto-clean -----------------------------------
    children.append(html.Div(
        className="outlier-row", children=[
            html.Div(id={"type": "outlier-badge", "index": idx},
                     className="outlier-badge-placeholder"),
            html.Button("Auto Clean",
                        id={"type": "auto-clean-btn", "index": idx},
                        n_clicks=0, className="btn-auto-clean"),
        ],
    ))

    # -- Shape Tools (collapsible) ------------------------------------
    children.append(_build_shape_section(idx, chart_type, ss))

    # -- Test rows ----------------------------------------------------
    children.append(_build_test_list(idx, tests, test_visibility))

    return html.Div(className="group-card virtual-group-card",
                    style={"borderColor": color},
                    children=children)


# =====================================================================
# SHARED CARD COMPONENTS
# =====================================================================

def _build_fit_controls(idx, chart_type, fs=None):
    """Fit controls: poly order, offset, fit range, show-all."""
    if fs is None:
        fs = {}
    init_poly = fs.get("poly_order", 3)
    init_offset = fs.get("offset_pct", 0)
    init_range = fs.get("bep_range_pct", [0,100])

    return html.Div(className="fit-controls", children=[
        html.Div(className="row space-between align-center", children=[
            html.Div("Line of Best Fit", className="fit-header"),
            html.Button("Show All",
                        id={"type": "card-show-all-btn", "index": idx},
                        n_clicks=0, className="btn-small"),
        ]),
        html.Div(className="fit-row", children=[
            html.Label("Poly Order:", className="fit-label"),
            dcc.Dropdown(
                id={"type": "fit-poly", "index": idx, "chart": chart_type},
                options=[{"label": str(i), "value": i}
                         for i in range(1, 7)],
                value=init_poly, clearable=False,
                className="fit-dropdown",
            ),
        ]),
        html.Div(className="fit-row", children=[
            html.Label("Offset %:", className="fit-label"),
            dcc.Input(
                id={"type": "fit-offset", "index": idx, "chart": chart_type},
                type="number", value=init_offset,
                min=-20, max=20, step=0.5,
                className="fit-input",
            ),
        ]),
        html.Div(className="slider-row compact-slider", children=[
            html.Div(className="row space-between", children=[
                html.Label("Fit Range %:", className="fit-label"),
                html.Span(
                    id={"type": "bep-range-readout", "index": idx, "chart": chart_type},
                    className="accent-value"),
            ]),
            dcc.RangeSlider(
                id={"type": "bep-range", "index": idx, "chart": chart_type},
                min=0, max=100, step=1, value=init_range,
                marks={0: "0%", 25: "25%", 50: "50%",
                       75: "75%", 100: "100%"},
                allowCross=False,
                tooltip={"placement": "bottom",
                         "always_visible": False},
            ),
        ]),
            # -- Iteration Selector ------------------------------------------
        html.Div(className="slider-row compact-slider", children=[
            html.Div(className="row space-between", children=[
                html.Label("Iteration:", className="fit-label"),
                html.Span(
                    id={"type": "iteration-readout", "index": idx},
                    className="accent-value"),
            ]),
            dcc.Slider(id={
                "type": "iteration-selector",
                "index": idx},
                min=1, max=3, step=1, value=3,
                marks={1: "1", 2: "2", 3: "All"},
                tooltip={"placement": "bottom", "always_visible": False})
        ])
    ])



def _build_shape_section(idx, chart_type, ss=None):
    """Collapsible shape tools section inside a trim card."""
    if ss is None:
        ss = {}
    init_droop_on = ["on"] if ss.get("droop_enabled", False) else []
    init_droop_pct = ss.get("droop_pct", 3)
    init_carry_on = ["on"] if ss.get("carryout_enabled", False) else []
    init_carry_pct = ss.get("carryout_pct", 3)
    init_spline_on = ["on"] if ss.get("spline_on", False) else []
    init_smooth = ss.get("smoothing", 0.3)
    
    return html.Details(className="shape-collapse", children=[
        html.Summary(className="shape-toggle", children=[
            html.Span("⚙", className="shape-toggle-icon"),
            html.Span("Curve Shape Tools"),
            html.Span("▾", className="shape-toggle-arrow"),
        ]),
        html.Div(className="shape-inner", children=[
            # -- Shutoff Adjustment --------------------------------
            html.Div(className="shape-section", children=[
                html.Div("Shutoff Adjustment",
                         className="shape-section-title"),
                html.Div(className="fit-row", children=[
                    dcc.Checklist(
                        id={"type": "shape-droop-on", "index": idx, "chart": chart_type},
                        options=[{"label": " Enable", "value": "on"}],
                        value=init_droop_on, className="fit-check",
                    ),
                    dcc.Input(
                        id={"type": "shape-droop-pct", "index": idx, "chart": chart_type},
                        type="number", value=init_droop_pct, min=-15, max=15,
                        step=0.5, className="fit-input",
                    ),
                    html.Span("%", className="fit-unit"),
                ]),
                html.Div("Lever fit near shutoff. "
                         "+ lowers, − raises.",
                         className="hint-text"),
            ]),

            # -- Carryout Adjustment -------------------------------
            html.Div(className="shape-section", children=[
                html.Div("Carryout Adjustment",
                         className="shape-section-title"),
                html.Div(className="fit-row", children=[
                    dcc.Checklist(
                        id={"type": "shape-carryout-on", "index": idx, "chart": chart_type},
                        options=[{"label": " Enable", "value": "on"}],
                        value=init_carry_on, className="fit-check",
                    ),
                    dcc.Input(
                        id={"type": "shape-carryout-pct", "index": idx, "chart": chart_type},
                        type="number", value=init_carry_pct, min=-15, max=15,
                        step=0.5, className="fit-input",
                    ),
                    html.Span("%", className="fit-unit"),
                ]),
                html.Div("Lever fit near max flow. "
                         "+ raises, − lowers.",
                         className="hint-text"),
            ]),

            # -- Spline mode ---------------------------------------
            html.Div(className="shape-section", children=[
                html.Div("Spline Fit Mode",
                         className="shape-section-title"),
                dcc.Checklist(
                    id={"type": "shape-spline-on", "index": idx, "chart": chart_type},
                    options=[{"label": " Use spline instead of polynomial",
                              "value": "on"}],
                    value=init_spline_on, className="fit-check",
                ),
                html.Div(className="slider-row compact-slider",
                         children=[
                    html.Div(className="row space-between", children=[
                        html.Label("Smoothing:", className="fit-label"),
                        html.Span(
                            id={"type": "shape-smooth-readout",
                                "index": idx},
                            className="accent-value"),
                    ]),
                    dcc.Slider(
                        id={"type": "shape-smoothing", "index": idx, "chart": chart_type},
                        min=0, max=1, step=0.05, value=init_smooth,
                        marks={0: "Tight", 0.5: "Med", 1: "Smooth"},
                        tooltip={"placement": "bottom",
                                 "always_visible": False},
                    ),
                ]),
            ]),
            
            # -- Knots ---------------------------------------------
            html.Div(className="shape-section", children=[
                html.Div("Knots", className="shape-section-title"),
                html.Div("Place knots to control the curve locally.",
                         className="hint-text"),
                _build_knot_rows(idx,chart_type,ss),
            ]),
        ]),
    ])


def _build_knot_rows(group_idx, chart_type, ss):
    """Build the knot input table for one group."""
    
    knots = ss.get("knots", [])
    rows = []
    
    for ki in range(MAX_KNOTS):
        kid = f"{group_idx}-{ki}"
        # Pre-fill from saved knots if available
        knot_flow = None
        knot_nudge = 0
        if ki < len(knots):
            knot_flow = knots[ki].get("flow")
            knot_nudge = knots[ki].get("nudge_pct", 0)
            print(
                f"ki={ki}, flow={knot_flow}, nudge={knot_nudge}"
                )
        rows.append(
            html.Div(className="knot-row", children=[
                html.Span(f"K{ki + 1}", className="knot-label"),
                dcc.Input(
                    id={"type": "knot-flow", "index": kid, "chart": chart_type},
                    type="number", placeholder="GPM",
                    value=knot_flow,
                    min=0, step=10, className="knot-input",
                ),
                dcc.Input(
                    id={"type": "knot-nudge", "index": kid, "chart": chart_type},
                    type="number", placeholder="±%",
                    value=knot_nudge, min=-15, max=15, step=0.1,
                    className="knot-input narrow",
                ),
            ]),
        )

    return html.Div(className="knot-table", children=[
        html.Div(className="knot-row knot-header-row", children=[
            html.Span("", className="knot-label"),
            html.Span("Flow (GPM)", className="knot-col-header"),
            html.Span("Nudge %",
                      className="knot-col-header narrow"),
        ]),
        *rows,
    ])


def _build_test_list(idx, tests, test_visibility=None):
    """Build the test row list for a card."""
    tv = test_visibility or {}
    has_filing = any(t.get("filing_info", "") for t in tests
                     if t.get("has_data"))

    test_rows = []
    for ti, t in enumerate(tests):
        if not t.get("has_data"):
            continue

        pf = str(t.get("pass_fail", ""))
        pf_cls = ("pf-pass" if pf.lower() in ("pass", "yes", "true")
                  else "pf-fail" if pf.lower() in ("fail", "no", "false")
                  else "pf-unk")
        pf_char = ("P" if "pass" in pf.lower() or "yes" in pf.lower()
                   else "F" if "fail" in pf.lower() or "no" in pf.lower()
                   else "?")

        vis_key = f"{idx}-{ti}"
        is_checked = tv.get(vis_key, True)

        trim_text = f'{t.get("trim_diameter", 0):.3f}"'
        filing = t.get("filing_info", "")
        test_date = str(t.get("test_date", ""))[:10]

        row_children = [
            dcc.Checklist(
                id={"type": "test-vis", "index": vis_key},
                options=[{"label": "", "value": "on"}],
                value=["on"] if is_checked else [],
                className="mini-check",
            ),
            html.Span(pf_char, className=f"pf-badge {pf_cls}"),
        ]

        # Polished impeller badge, vertical pumps detected by ##RA in trim
        if t.get("is_polished"):
            row_children.append(
                html.Span(
                    "RA",
                    className="polished-badge",
                    title="Polished impeller",
                )
            )

        # Mixed trim badge, vertical pumps with multiple trim groups
        if t.get("mixed_trim"):
            row_children.append(
                html.Span(
                    "MT",
                    className="mixed-trim-badge",
                    title="Mixed trim: multiple impeller trim groups found in Top Impeller Trim",
                )
            )

        row_children.extend([
            html.Span(
                t.get("test_id", "")[:22],
                title=t.get("test_id", ""),
                className="test-id-text",
            ),
            html.Span(trim_text, className="test-trim-text"),
            html.Span(test_date, className="test-date-text"),
        ])

        if has_filing and filing:
            row_children.append(
                html.Span(
                    filing,
                    className="test-filing-text",
                    title=filing,
                )
            )

        test_rows.append(
            html.Div(className="test-row", children=row_children)
        )

    no_data = sum(1 for t in tests if not t.get("has_data"))
    if no_data > 0:
        test_rows.append(
            html.Div(
                f"+ {no_data} test(s) without detail data",
                className="no-data-hint",
            )
        )

    if test_rows:
        return html.Div(className="test-list", children=test_rows)

    return html.Div(
        "No detail data loaded",
        className="empty-message",
    )

# =====================================================================
# SHAPE TOOLS CARD BUILDER (kept for compatibility — builds from card)
# =====================================================================

def build_shape_tools_card(idx, grp):
    """Compatibility stub — shape tools are now inside trim cards."""
    return html.Div()


# =====================================================================
# HELPERS
# =====================================================================

def _section(title, children):
    return html.Div(
        className="section",
        children=[
            html.Div(title, className="section-header"),
            *children,
        ],
    )

def build_drawer(title, description, content_id, panel_class):
    return html.Div(
        className=f"right-panel {panel_class}",
        children=[
            html.Div(
                className="row space-between align-center mb-8",
                children=[
                    html.Span(title, className="panel-title"),
                ],
            ),
            html.Div(description, className="hint-text mb-8"),
            html.Div(
                id=content_id,
                children=[
                    html.P(
                        "Run a comparison first to see analysis.",
                        className="empty-message",
                    ),
                ],
            ),
        ],
    )