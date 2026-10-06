from dash import html, dcc

def build_controls(available_keys, column_options):
    return html.Div([
        # Dataset selector
        dcc.Dropdown(
            id="dataset-selector",
            options=[{"label": k, "value": k} for k in available_keys],
            multi=True,
            placeholder="Select datasets..."
        ),

        # Dynamic range slider
        html.Label("Value Range"),
        dcc.RangeSlider(id="value-range", min=0, max=100, value=[0, 100],
                        tooltip={"placement": "bottom"}),

        # Date range
        dcc.DatePickerRange(id="date-range"),

        # Column selector for Y-axis
        dcc.Dropdown(id="y-axis-selector", options=column_options,
                     multi=True, placeholder="Select columns to plot..."),

        # Chart type toggle
        dcc.RadioItems(
            id="chart-type",
            options=["Line", "Scatter", "Bar", "Area"],
            value="Line",
            inline=True
        ),

        # Annotation toggle
        html.Button("Enable Annotations", id="annotation-toggle", n_clicks=0),

        # Export
        html.Button("Export to Excel", id="export-btn", n_clicks=0),
        dcc.Download(id="download-export"),
    ])