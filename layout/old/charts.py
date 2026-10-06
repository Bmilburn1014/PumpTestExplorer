from dash import dcc

def build_chart_area():
    return dcc.Graph(
        id="main-chart",
        config={
            "scrollZoom": True,
            "modeBarButtonsToAdd": [
                "drawline", "drawrect", "drawopenpath", "eraseshape",
            ],
            "toImageButtonOptions": {
                "format": "png", "width": 1200, "height": 800,
            },
        },
        style={"height": "70vh"},
    )