import { useEffect, useRef, useState } from "react";

import { getMapConfig } from "./api";
import type { Plan, WebMapConfig } from "./types";

type Position = [number, number];
type Overlay = object;

type AmapMapInstance = {
  destroy: () => void;
  setFitView: (
    overlays: Overlay[],
    immediately?: boolean,
    avoid?: [number, number, number, number],
    maxZoom?: number,
  ) => void;
};

type AmapNamespace = {
  Map: new (container: HTMLElement, options: Record<string, unknown>) => AmapMapInstance;
  Marker: new (options: Record<string, unknown>) => Overlay;
  Polyline: new (options: Record<string, unknown>) => Overlay;
};

declare global {
  interface Window {
    AMap?: AmapNamespace;
    _AMapSecurityConfig?: { serviceHost: string };
  }
}

let loader: Promise<AmapNamespace> | null = null;

function loadAmap(config: WebMapConfig): Promise<AmapNamespace> {
  if (window.AMap) return Promise.resolve(window.AMap);
  if (loader) return loader;
  if (!config.js_api_key || !config.version || !config.service_host_path) {
    return Promise.reject(new Error("高德地图浏览器配置不完整"));
  }

  window._AMapSecurityConfig = {
    serviceHost: new URL(config.service_host_path, window.location.origin).toString(),
  };
  loader = new Promise<AmapNamespace>((resolve, reject) => {
    const script = document.createElement("script");
    script.src = `https://webapi.amap.com/maps?v=${encodeURIComponent(config.version!)}&key=${encodeURIComponent(config.js_api_key!)}`;
    script.async = true;
    script.dataset.hftAmap = "true";
    script.onload = () => {
      if (window.AMap) resolve(window.AMap);
      else reject(new Error("高德 JS API 已加载但未提供 AMap 对象"));
    };
    script.onerror = () => reject(new Error("高德 JS API 加载失败"));
    document.head.appendChild(script);
  }).catch((error) => {
    loader = null;
    throw error;
  });
  return loader;
}

function legPath(plan: Plan): Position[][] {
  return plan.route_legs
    .map((leg) =>
      leg.geometry
        .filter((point) => Number.isFinite(point.longitude) && Number.isFinite(point.latitude))
        .map((point): Position => [point.longitude, point.latitude]),
    )
    .filter((path) => path.length >= 2);
}

function renderPlan(
  container: HTMLElement,
  plan: Plan,
  AMap: AmapNamespace,
): AmapMapInstance {
  const paths = legPath(plan);
  if (!paths.length) throw new Error("当前方案没有可绘制的路线坐标");

  const map = new AMap.Map(container, {
    viewMode: "2D",
    mapStyle: "amap://styles/whitesmoke",
    zoom: 12,
  });
  const overlays: Overlay[] = [];
  paths.forEach((path) => {
    overlays.push(
      new AMap.Polyline({
        map,
        path,
        strokeColor: "#3178c6",
        strokeWeight: 6,
        strokeOpacity: 0.88,
        lineJoin: "round",
        showDir: true,
      }),
    );
  });

  const markerPositions = [paths[0][0], ...paths.map((path) => path[path.length - 1])];
  markerPositions.forEach((position, index) => {
    overlays.push(
      new AMap.Marker({
        map,
        position,
        anchor: "center",
        title: index === 0 ? "出发地" : plan.stops[index - 1]?.name,
        content: `<div class="amap-plan-marker ${index === 0 ? "origin" : ""}">${index === 0 ? "起" : index}</div>`,
      }),
    );
  });
  map.setFitView(overlays, false, [42, 32, 42, 32], 15);
  return map;
}

export function AmapPlanMap({ plan }: { plan: Plan }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "disabled" | "error">("loading");
  const [message, setMessage] = useState("正在加载高德地图…");

  useEffect(() => {
    let disposed = false;
    let map: AmapMapInstance | null = null;
    setStatus("loading");
    setMessage("正在加载高德地图…");

    getMapConfig()
      .then(async (config) => {
        if (!config.enabled || config.provider !== "amap") {
          if (!disposed) {
            setStatus("disabled");
            setMessage("未配置高德 JS API，当前仅显示路线文字摘要");
          }
          return;
        }
        const AMap = await loadAmap(config);
        if (disposed || !containerRef.current) return;
        map = renderPlan(containerRef.current, plan, AMap);
        setStatus("ready");
      })
      .catch((error: unknown) => {
        if (!disposed) {
          setStatus("error");
          setMessage(error instanceof Error ? error.message : "高德地图加载失败");
        }
      });

    return () => {
      disposed = true;
      map?.destroy();
    };
  }, [plan]);

  return (
    <div className="map-canvas amap-map-shell" aria-label="高德地图路线图">
      <div className="amap-map-container" ref={containerRef} />
      {status !== "ready" ? <div className={`amap-map-status ${status}`}>{message}</div> : null}
    </div>
  );
}
