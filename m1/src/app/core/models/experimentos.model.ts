export type ClaseUrgencia = 'muy_baja' | 'baja' | 'media' | 'alta' | 'critica';

export interface PedidoExperimento {
  id: string; id_sku?: string; urgencia: string; puntuacion?: number | null; plazo_tick: number | null;
  ola?: number | null; liberacion: number | null; asignacion: number | null; entrega: number | null;
}
export interface OlaExperimento {
  numero: number; pedidos: string[]; preparacion_inicio: number;
  preparacion_ticks: number; liberacion: number | null; fin: number | null;
}
export interface ServicioClase {
  total: number; entregados: number; a_tiempo: number; con_plazo?:number;
  cumplimiento?: number | null; cumplimiento_plazo?:number|null;
  tiempo_medio_entrega?: number | null; tiempo_promedio_ticks?:number|null;
}
export interface PreparacionFisica {
  fase?: string; caja?: string | null; robot?: number | string | null;
  origen?: {x:number;y:number;z:number} | null; destino?: {x:number;y:number;z:number} | null;
  pendientes?: number | unknown[]; motivo?: string | null;
}
export interface PlanExperimento {
  modelo_preparacion?: 'fisica' | 'sintetica';
  tiempo: number; completa: boolean; tiempo_finalizacion: number | null; adaptativa?:boolean;
  preparacion_total: number; estado_ola: string; tamano: number; regla: string;
  urgentes_total: number; urgentes_a_tiempo: number; olas: OlaExperimento[];
  pedidos?: PedidoExperimento[]; total_pedidos?: number; total_olas?: number;
  servicio_por_categoria?: Partial<Record<ClaseUrgencia, ServicioClase>>;
  preparacion_fisica?: PreparacionFisica | null;
}
export interface GeneradorInstanciaRequest {
  version: 2; semilla:number; cantidad:number; productos:number; x:number|null; y:number|null; z:number;
  ocupacion:number; robots:number; media_urgencia:number; desviacion_urgencia:number;
  plazo_base:number|null; perfil:'uniforme'|'concentrado';
}
export interface SolicitudInstancia {
  pedido: {id_pedido:string;id_sku:string}; orden:number; urgencia:string;
  puntuacion?:number; plazo_tick:number|null;
}
export interface InstanciaV2 {
  version:number; semilla:number; parametros?:{frecuencias_esperadas?:Partial<Record<ClaseUrgencia,number>>;frecuencias_observadas?:Partial<Record<ClaseUrgencia,number>>;[key:string]:unknown};
  config:{grilla:{x:number;y:number;z:number};robots:number;ocupacion_inicial?:number};
  inventario?:{id_caja:string;id_sku:string;x:number;y:number;z:number}[];
  solicitudes:SolicitudInstancia[];
}
export interface InstanciaDoc { id:string; instancia:InstanciaV2; }
export interface InstanciaResumen {id:string; semilla:number; total:number; urgentes:number; clases?:Partial<Record<ClaseUrgencia,number>>; version?:number;}
export interface CorridaResumen {id:string; instancia_id:string; creada:string; estado:string; tiempo:number; entregados:number; total:number; version?:number; modelo_preparacion?:string;}
export interface CorridaDoc {
  entrada: {instancia_id:string; limite_ticks:number; opciones: {tamano:number; regla:string; adaptativa?:boolean; preparacion_fija:number; preparacion_por_pedido:number; modelo_preparacion?:string}; instancia: InstanciaV2};
  resultado: CorridaResumen & {motivo:string|null; tiempo_finalizacion:number|null; preparacion_total:number;
    urgentes_a_tiempo:number; urgentes_total:number; movimientos_bloqueadores:number;
    robot_ticks_excavacion:number; robot_ticks_espera_escort:number; robot_ticks_espera_espacio:number;
    servicio_por_categoria?:Partial<Record<ClaseUrgencia,ServicioClase>>;
    pedidos?:PedidoExperimento[]; olas:OlaExperimento[]};
  eventos?:unknown[];
}
export interface PaginaPedidos {items:PedidoExperimento[];pagina:number;tamano:number;total:number;}
export interface PaginaEventos {items:unknown[];desde:number;siguiente:number;hay_mas:boolean;}
export interface EstadoExperimental {status:string; experimento:PlanExperimento|null; corrida:{id:string;estado:string}|null;}
export interface OpcionesOlas {
  tamano:number; regla:string; preparacion_fija:number; preparacion_por_pedido:number;
  limite_ticks:number; ventana_sin_entregas:number|null; adaptativa:boolean;
  minimo:number; maximo:number; objetivo_ticks_por_pedido:number; modelo_preparacion:'fisica'|'sintetica';
}

export type EstadoLote='lista'|'ejecutando'|'cancelando'|'completa'|'cancelada'|'interrumpida'|'error';
export type EstadoConfiguracionLote='pendiente'|'ejecutando'|'registrada'|'omitida'|string;
export interface ConfiguracionLote {
  id:string; tamano:number; regla:string; estado:EstadoConfiguracionLote; corrida_id:string|null;
  resultado:null|{
    estado:string; motivo:string|null; entregados:number; total:number; tiempo:number;
    tiempo_finalizacion:number|null; preparacion_total:number; movimientos_preparacion:number;
    movimientos_bloqueadores:number; cumplimiento_urgentes:number|null;
    servicio_por_categoria?:Partial<Record<ClaseUrgencia,ServicioClase>>;
  };
}
export interface LoteResumen {
  id:string; nombre:string; instancia_id:string; semilla:number; total_pedidos:number;
  estado:EstadoLote; creada:string; total_configuraciones:number; finalizadas:number;
}
export interface LoteDoc extends LoteResumen {
  actual:number|null; configuraciones:ConfiguracionLote[];
}
export interface CrearLoteRequest {
  instancia_id:string; nombre:string|null; configuraciones:{tamano:number;regla:string}[];
  limite_ticks:number; ventana_sin_entregas:number|null;
}
