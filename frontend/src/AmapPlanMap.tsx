import { useEffect, useRef, useState } from "react";

import { getMapConfig } from "./api";
import type { Plan, WebMapConfig } from "./types";

type Position = [number, number];
type Overlay = {
  on?: (eventName: string, listener: () => void) => void;
};

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

function legPath(plan: Plan): Array<{ index: number; path: Position[] }> {
  return plan.route_legs
    .map((leg, index) => ({
      index,
      path: leg.geometry
        .filter((point) => Number.isFinite(point.longitude) && Number.isFinite(point.latitude))
        .map((point): Position => [point.longitude, point.latitude]),
    }))
    .filter(({ path }) => path.length >= 2);
}

function coordinatesForLeg(plan: Plan, index: number): Position[] {
  return (plan.route_legs[index]?.geometry ?? [])
    .filter((point) => Number.isFinite(point.longitude) && Number.isFinite(point.latitude))
    .map((point): Position => [point.longitude, point.latitude]);
}

function stopMarkerPositions(plan: Plan): Array<{ stopIndex: number; position: Position }> {
  return plan.stops.flatMap((_stop, stopIndex) => {
    // RouteLeg index N-1 arrives at Stop N-1. If that leg has no coordinates,
    // the following raw leg's origin can still locate the same stop.
    const arrivalPath = coordinatesForLeg(plan, stopIndex);
    const departurePath = coordinatesForLeg(plan, stopIndex + 1);
    const position = arrivalPath.at(-1) ?? departurePath[0];
    return position ? [{ stopIndex, position }] : [];
  });
}

function renderPlan(
  container: HTMLElement,
  plan: Plan,
  AMap: AmapNamespace,
  activeLegIndex: number | null,
  onFocusRoute: (legIndex: number) => void,
  onOpenPoi: (stopIndex: number) => void,
): AmapMapInstance {
  const legPaths = legPath(plan);
  if (!legPaths.length) throw new Error("当前方案没有可绘制的路线坐标");

  const map = new AMap.Map(container, {
    viewMode: "2D",
    mapStyle: "amap://styles/whitesmoke",
    zoom: 12,
  });
  const overlays: Overlay[] = [];
  legPaths.forEach(({ index, path }) => {
    const isActive = activeLegIndex === index;
    const polyline = new AMap.Polyline({
        map,
        path,
        strokeColor: isActive ? "#075a9f" : "#3178c6",
        strokeWeight: isActive ? 9 : 6,
        strokeOpacity: 0.88,
        lineJoin: "round",
        showDir: true,
      });
    polyline.on?.("click", () => onFocusRoute(index));
    overlays.push(polyline);
  });

  const originPosition = coordinatesForLeg(plan, 0)[0];
  if (originPosition) {
    const marker = new AMap.Marker({
      map,
      position: originPosition,
      anchor: "center",
      title: "出发地",
      content: '<div class="amap-plan-marker origin">出</div>',
    });
    overlays.push(marker);
  }

  stopMarkerPositions(plan).forEach(({ stopIndex, position }) => {
    const marker = new AMap.Marker({
      map,
      position,
      anchor: "center",
      title: plan.stops[stopIndex].name,
      content: `<div class="amap-plan-marker">${stopIndex + 1}</div>`,
      });
    marker.on?.("click", () => onOpenPoi(stopIndex));
    overlays.push(marker);
  });

  const returnLegIndex = plan.route_legs.length > plan.stops.length ? plan.route_legs.length - 1 : null;
  const returnPath = returnLegIndex === null ? [] : coordinatesForLeg(plan, returnLegIndex);
  const returnPosition = returnPath.at(-1);
  if (returnPosition) {
    const marker = new AMap.Marker({
      map,
      position: returnPosition,
      anchor: "center",
      title: "返程终点：出发地",
      content: '<div class="amap-plan-marker origin">返</div>',
    });
    overlays.push(marker);
  }
  map.setFitView(overlays, false, [42, 32, 42, 32], 15);
  return map;
}

export function AmapPlanMap({ plan, activeLegIndex, onFocusRoute, onOpenPoi }: { plan: Plan; activeLegIndex: number | null; onFocusRoute: (legIndex: number) => void; onOpenPoi: (stopIndex: number) => void }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "disabled" | "error">("loading");
  const [message, setMessage] = useState("正在加载高德地图…");

  useEffect(() => {
    let disposed = false;
    let map: AmapMapInstance | null = null;
    setStatus("loading");
    setMessage("正在加载高德地图…");

    if (!legPath(plan).length) {
      setStatus("disabled");
      setMessage("当前响应没有可绘制的有效路线坐标；可从下方站点列表查看 POI 与文字路线");
      return () => { disposed = true; };
    }

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
        map = renderPlan(containerRef.current, plan, AMap, activeLegIndex, onFocusRoute, onOpenPoi);
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
  }, [activeLegIndex, onFocusRoute, onOpenPoi, plan]);

  return (
    <div className="map-canvas amap-map-shell" aria-label="高德地图路线图">
      <div className="amap-map-container" ref={containerRef} />
      {status !== "ready" ? <div className={`amap-map-status ${status}`}>{message}</div> : null}
    </div>
  );
}
