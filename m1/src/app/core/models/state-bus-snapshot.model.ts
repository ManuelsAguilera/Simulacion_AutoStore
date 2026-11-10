// Contrato real del bridge WebSocket (§3.1 handoff_integracion_m1.md)
// Cada mensaje en ws://localhost:8000/ws/state tiene este formato.

export interface WsRobotState {
  id:           number;
  x:            number;
  y:            number;
  z:            number;
  estado:       string;
  carga_id:     string | null;
  orientacion?: string;   // 'N' | 'E' | 'O' — robots 1×2 con orientación fija
}

export interface WsEstacion {
  x:           number;
  y:           number;
  orientacion: string;    // 'E' (borde Este) | 'O' (borde Oeste)
}

export interface WsConveyor {        // conveyor de ingreso (Norte)
  x: number;
  y: number;
}

export interface WsInterior {        // límites del almacenaje (inclusivos)
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

export interface WsGrillaCell {
  id_caja:  string;
  id_sku:   string;
  cantidad: number;
  x:        number;
  y:        number;
  z:        number;
}

export interface WsKpis {
  // Claves en MAYÚSCULA (usadas por la UI: KpisComputed en bus-client.service.ts)
  TSP: number; TPCP: number; MTRP: number; IOG: number; TR: number; TI: number; TBR: number;
  // Claves en minúscula (alias enviados por el bridge)
  tsp: number; tpcp: number; mtrp: number; iog: number; tr: number; ti: number; tbr: number;
  completados:   number;
  capacidad:     number;
  cajasPresentes: number;
}

import { PlanExperimento } from './experimentos.model';
export interface WsTickPayload {
  experimento?: PlanExperimento|null;
  type:      'tick';
  tick:      number;
  mode:      string;
  policy:    string;
  status:    string;
  velocidad: number;
  grid:      { x: number; y: number; z: number } | null;
  gridTotal?: { x: number; y: number } | null;   // superficie total transitable (con corredores)
  interior?: WsInterior | null;                   // límites del almacenaje
  estaciones?: WsEstacion[];                      // estaciones de salida (E/O)
  conveyorsNorte?: WsConveyor[];                  // conveyors de ingreso (Norte)
  robots:    WsRobotState[];
  grilla:    WsGrillaCell[];
  pedidos:   { cola: unknown[]; completados: unknown[] };
  kpis:      WsKpis;
}

export interface WsSystemErrorPayload {
  type:      'system_error';
  component: string;
  message:   string;
}

export type WsMessage = WsTickPayload | WsSystemErrorPayload;
