"use client";

import { useEffect, useRef, useState } from "react";
import {
  Map as MLMap,
  NavigationControl,
  Popup,
  type ExpressionSpecification,
  type GeoJSONSource,
  type StyleSpecification,
} from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import type { NodeCoordinate, RouteEdge } from "@/lib/types";

const NODE_TYPE_ORDER: NodeCoordinate["node_type"][] = ["Supplier", "Warehouse", "Factory", "Customer"];

function nodeColor(type: NodeCoordinate["node_type"]): string {
  switch (type) {
    case "Supplier":
      return "#2f5068";
    case "Warehouse":
      return "#1a1d21";
    case "Factory":
      return "#8a6212";
    case "Customer":
      return "#3f6b47";
  }
}

const BASE_STYLE: StyleSpecification = {
  version: 8,
  sources: {},
  layers: [
    {
      id: "background",
      type: "background",
      paint: { "background-color": "#f7f7f4" },
    },
  ],
};

export function MapPanel({
  nodes,
  routes,
  daysOfCoverByNode,
  activeRouteIds,
  onSelectLane,
  selectedLaneId,
}: {
  nodes: NodeCoordinate[];
  routes: RouteEdge[];
  daysOfCoverByNode: Map<string, number>;
  activeRouteIds: Set<string>;
  onSelectLane: (routeId: string | null) => void;
  selectedLaneId: string | null;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MLMap | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    const map = new MLMap({
      container: containerRef.current,
      style: BASE_STYLE,
      center: [78.9, 20.5],
      zoom: 3.9,
      attributionControl: false,
    });
    map.addControl(new NavigationControl({ showCompass: false }), "top-right");
    map.on("load", () => {
      // React 18 StrictMode dev double-invokes this effect: the first map
      // gets torn down before its async "load" fires. Only trust it if
      // this is still the current instance, or the second map's sources
      // can get added before its own style has actually finished loading.
      if (mapRef.current === map) setReady(true);
    });
    mapRef.current = map;
    return () => {
      map.remove();
      mapRef.current = null;
      setReady(false);
    };
  }, []);

  // lanes as arcs
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready || nodes.length === 0) return;
    const nodeById = new Map(nodes.map((n) => [n.node_id, n]));

    const seen = new Set<string>();
    const features = routes
      .filter((r) => {
        const key = `${r.origin}-${r.destination}-${r.mode}`;
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      })
      .map((r) => {
        const o = nodeById.get(r.origin);
        const d = nodeById.get(r.destination);
        if (!o || !d) return null;
        const midLon = (o.longitude + d.longitude) / 2;
        const midLat = (o.latitude + d.latitude) / 2 + Math.abs(o.longitude - d.longitude) * 0.08;
        return {
          type: "Feature" as const,
          properties: {
            route_id: r.route_id,
            status: r.route_status,
            mode: r.mode,
            distance_km: r.distance_km,
            active: activeRouteIds.has(r.route_id) ? 1 : 0,
          },
          geometry: {
            type: "LineString" as const,
            coordinates: [
              [o.longitude, o.latitude],
              [midLon, midLat],
              [d.longitude, d.latitude],
            ],
          },
        };
      })
      .filter((f): f is NonNullable<typeof f> => f !== null);

    const source = map.getSource("lanes") as GeoJSONSource | undefined;
    const data = { type: "FeatureCollection" as const, features };
    if (source) {
      source.setData(data);
    } else {
      const LINE_COLOR: ExpressionSpecification = [
        "match",
        ["get", "status"],
        "CLOSED",
        "#9e2f2f",
        "CONGESTED",
        "#8a6212",
        "#3f6b47",
      ];
      const LINE_WIDTH: ExpressionSpecification = [
        "interpolate",
        ["linear"],
        ["get", "distance_km"],
        50,
        1,
        2000,
        3.5,
      ];
      const LINE_OPACITY: ExpressionSpecification = ["case", ["==", ["get", "active"], 1], 0.95, 0.35];

      map.addSource("lanes", { type: "geojson", data });

      // line-dasharray is not a data-driven property in the MapLibre style
      // spec (constants/zoom-expressions only), so one layer per mode with
      // its own constant dasharray, rather than a single data-driven layer.
      const MODE_DASH: [string, number[] | undefined][] = [
        ["Truck_Diesel", undefined],
        ["Truck_Electric", [0.5, 1.5]],
        ["Rail", [4, 2]],
        ["Air", [1, 2]],
      ];
      for (const [mode, dasharray] of MODE_DASH) {
        map.addLayer({
          id: `lanes-line-${mode}`,
          type: "line",
          source: "lanes",
          filter: ["==", ["get", "mode"], mode],
          paint: {
            "line-color": LINE_COLOR,
            "line-width": LINE_WIDTH,
            "line-opacity": LINE_OPACITY,
            ...(dasharray ? { "line-dasharray": dasharray } : {}),
          },
          layout: { "line-cap": "round", "line-join": "round" },
        });
        map.on("click", `lanes-line-${mode}`, (e) => {
          const rid = e.features?.[0]?.properties?.route_id as string | undefined;
          if (rid) onSelectLane(rid);
        });
        map.on("mouseenter", `lanes-line-${mode}`, () => {
          map.getCanvas().style.cursor = "pointer";
        });
        map.on("mouseleave", `lanes-line-${mode}`, () => {
          map.getCanvas().style.cursor = "";
        });
      }
    }
  }, [ready, nodes, routes, activeRouteIds, onSelectLane]);

  // node rings
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready || nodes.length === 0) return;

    const features = nodes.map((n) => {
      const cover = daysOfCoverByNode.get(n.node_id);
      let riskColor = "#2f5068";
      if (cover !== undefined) {
        riskColor = cover < 2 ? "#9e2f2f" : cover < 5 ? "#8a6212" : "#3f6b47";
      }
      return {
        type: "Feature" as const,
        properties: {
          node_id: n.node_id,
          node_type: n.node_type,
          city: n.city,
          color: nodeColor(n.node_type),
          risk_color: riskColor,
          radius: cover !== undefined ? Math.min(14, 5 + cover / 3) : 5,
        },
        geometry: { type: "Point" as const, coordinates: [n.longitude, n.latitude] },
      };
    });

    const source = map.getSource("nodes") as GeoJSONSource | undefined;
    const data = { type: "FeatureCollection" as const, features };
    if (source) {
      source.setData(data);
      return;
    }
    map.addSource("nodes", { type: "geojson", data });
    map.addLayer({
      id: "nodes-ring",
      type: "circle",
      source: "nodes",
      paint: {
        "circle-radius": ["get", "radius"],
        "circle-color": "transparent",
        "circle-stroke-width": 2.5,
        "circle-stroke-color": ["get", "risk_color"],
      },
    });
    map.addLayer({
      id: "nodes-dot",
      type: "circle",
      source: "nodes",
      paint: {
        "circle-radius": 3,
        "circle-color": ["get", "color"],
      },
    });

    const popup = new Popup({ closeButton: false, closeOnClick: false });
    map.on("mouseenter", "nodes-dot", (e) => {
      map.getCanvas().style.cursor = "pointer";
      const f = e.features?.[0];
      if (!f) return;
      const props = f.properties as { city: string; node_type: string; node_id: string };
      popup
        .setLngLat((f.geometry as GeoJSON.Point).coordinates as [number, number])
        .setHTML(`<div style="font-size:11px"><strong>${props.node_id}</strong> &middot; ${props.node_type}<br/>${props.city}</div>`)
        .addTo(map);
    });
    map.on("mouseleave", "nodes-dot", () => {
      map.getCanvas().style.cursor = "";
      popup.remove();
    });
  }, [ready, nodes, daysOfCoverByNode]);

  // highlight selected lane
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    const width: ExpressionSpecification = selectedLaneId
      ? [
          "case",
          ["==", ["get", "route_id"], selectedLaneId],
          5,
          ["interpolate", ["linear"], ["get", "distance_km"], 50, 1, 2000, 3.5],
        ]
      : ["interpolate", ["linear"], ["get", "distance_km"], 50, 1, 2000, 3.5];
    for (const mode of ["Truck_Diesel", "Truck_Electric", "Rail", "Air"]) {
      const layerId = `lanes-line-${mode}`;
      if (map.getLayer(layerId)) map.setPaintProperty(layerId, "line-width", width);
    }
  }, [ready, selectedLaneId]);

  return (
    <div className="relative h-full min-h-0 w-full">
      <div ref={containerRef} className="h-full w-full" />
      <div className="pointer-events-none absolute bottom-2 left-2 flex flex-col gap-1 rounded border border-rule bg-surface/95 px-2 py-1.5 text-11">
        {NODE_TYPE_ORDER.map((t) => (
          <div key={t} className="flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-full" style={{ backgroundColor: nodeColor(t) }} />
            <span className="text-ink/70">{t}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
