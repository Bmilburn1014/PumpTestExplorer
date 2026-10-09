/* Drag the 25 spline knots on the head, power, and combined charts.
   A mouseup writes the new positions into the knot-drag-store. */
(function () {
    var GRAPH_IDS = ["main-chart", "combined-chart"];

    function plotNode(id) {
        var root = document.getElementById(id);
        if (!root) return null;
        if (root.classList && root.classList.contains("js-plotly-plot")) return root;
        return root.querySelector(".js-plotly-plot");
    }

    function isKnot(point) {
        return point && point.data &&
            String(point.data.name || "").indexOf("spline-knots|") === 0;
    }

    function bind(gd) {
        if (!gd || gd.__knotBound) return;
        gd.__knotBound = true;
        gd.__knotHover = null;

        gd.on("plotly_hover", function (ev) {
            var pt = ev.points && ev.points[0];
            if (!isKnot(pt)) {
                if (!gd.__knotDragging) gd.__knotHover = null;
                return;
            }
            gd.__knotHover = {
                curve: pt.curveNumber,
                point: pt.pointNumber,
                name: pt.data.name,
                xaxis: pt.xaxis,
                yaxis: pt.yaxis
            };
            gd.style.cursor = "grab";
        });
        gd.on("plotly_unhover", function () {
            if (!gd.__knotDragging) {
                gd.__knotHover = null;
                gd.style.cursor = "";
            }
        });

        gd.addEventListener("mousedown", function (evt) {
            if (!gd.__knotHover) return;
            gd.__knotDragging = gd.__knotHover;
            gd.style.cursor = "grabbing";
            evt.preventDefault();
            evt.stopPropagation();
        }, true);
    }

    function dataFromPixel(gd, drag, evt) {
        var bb = gd.getBoundingClientRect();
        var xax = gd._fullLayout[drag.xaxis._id];
        var yax = gd._fullLayout[drag.yaxis._id];
        if (!xax || !yax || !xax.p2d || !yax.p2d) return null;
        var x = xax.p2d(evt.clientX - bb.left - xax._offset);
        var y = yax.p2d(evt.clientY - bb.top - yax._offset);
        if (!isFinite(x) || !isFinite(y)) return null;
        return [x, y];
    }

    window.addEventListener("mousemove", function (evt) {
        GRAPH_IDS.forEach(function (id) {
            var gd = plotNode(id);
            if (!gd || !gd.__knotDragging) return;
            var drag = gd.__knotDragging;
            var xy = dataFromPixel(gd, drag, evt);
            if (!xy) return;
            var trace = gd.data[drag.curve];
            if (!trace) return;
            var xs = Array.prototype.slice.call(trace.x);
            var ys = Array.prototype.slice.call(trace.y);
            xs[drag.point] = xy[0];
            ys[drag.point] = xy[1];
            drag.xs = xs;
            drag.ys = ys;
            window.Plotly.restyle(gd, {x: [xs], y: [ys]}, [drag.curve]);
        });
    });

    window.addEventListener("mouseup", function () {
        GRAPH_IDS.forEach(function (id) {
            var gd = plotNode(id);
            if (!gd || !gd.__knotDragging) return;
            var drag = gd.__knotDragging;
            gd.__knotDragging = null;
            gd.style.cursor = "";
            var xs = drag.xs || (gd.data[drag.curve] && gd.data[drag.curve].x);
            var ys = drag.ys || (gd.data[drag.curve] && gd.data[drag.curve].y);
            if (!xs || !ys) return;
            var parts = String(drag.name || "").split("|");
            var knots = [];
            for (var i = 0; i < xs.length; i++) {
                knots.push({flow: xs[i], y: ys[i]});
            }
            if (window.dash_clientside && window.dash_clientside.set_props) {
                window.dash_clientside.set_props("knot-drag-store", {
                    data: {
                        chart: parts[1],
                        group: parts[2],
                        knots: knots,
                        t: Date.now()
                    }
                });
            }
        });
    });

    setInterval(function () {
        GRAPH_IDS.forEach(function (id) { bind(plotNode(id)); });
    }, 800);
})();
