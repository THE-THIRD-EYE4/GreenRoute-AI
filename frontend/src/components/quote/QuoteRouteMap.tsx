"use client";

import { useEffect, useRef } from "react";
import { Map as MLMap, Marker, type StyleSpecification } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import type { NodeCoordinate } from "@/lib/types";

const BASE_STYLE: StyleSpecification = {
  version: 8,
  sources: {},
  layers: [{ id: "background", type: "background", paint: { "background-color": "#f7f7f4" } }],
};

export function QuoteRouteMap({ nodes, stopSequence }: { nodes: NodeCoordinate[]; stopSequence: string[] }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MLMap | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    const map = new MLMap({
      container: containerRef.current,
      style: BASE_STYLE,
      center: [78.9, 20.5],
      zoom: 3.6,
      attributionControl: false,
    });
    mapRef.current = map;
    return () => {
      map.remove();
      mapRef.current = null;
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || stopSequence.length === 0) return;
    const nodeById = new Map(nodes.map((n) => [n.node_id, n]));
    const coords = stopSequence
      .map((id) => nodeById.get(id))
      .filter((n): n is NodeCoordinate => !!n)
      .map((n) => [n.longitude, n.latitude] as [number, number]);
    if (coords.length === 0) return;

    const draw = () => {
      if (map.getLayer("route-line")) map.removeLayer("route-line");
      if (map.getSource("route")) map.removeSource("route");
      map.addSource("route", {
        type: "geojson",
        data: { type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: coords } },
      });
      map.addLayer({
        id: "route-line",
        type: "line",
        source: "route",
        paint: { "line-color": "#2f5068", "line-width": 3 },
        layout: { "line-cap": "round" },
      });

      coords.forEach((c, i) => {
        const el = document.createElement("div");
        el.style.width = "10px";
        el.style.height = "10px";
        el.style.borderRadius = "50%";
        el.style.background = i === 0 ? "#3f6b47" : i === coords.length - 1 ? "#9e2f2f" : "#2f5068";
        el.style.border = "2px solid white";
        new Marker({ element: el }).setLngLat(c).addTo(map);
      });
    };

    if (map.isStyleLoaded()) {
      draw();
    } else {
      map.once("load", draw);
    }

    if (coords.length > 1) {
      const lons = coords.map((c) => c[0]);
      const lats = coords.map((c) => c[1]);
      map.fitBounds(
        [
          [Math.min(...lons), Math.min(...lats)],
          [Math.max(...lons), Math.max(...lats)],
        ],
        { padding: 40, duration: 0 },
      );
    } else {
      map.setCenter(coords[0]);
      map.setZoom(6);
    }
  }, [nodes, stopSequence]);

  return <div ref={containerRef} className="h-full w-full" />;
}
