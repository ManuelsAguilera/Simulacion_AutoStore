import { Component, Input, OnInit, OnDestroy } from '@angular/core';
import { DatePipe, DecimalPipe, JsonPipe } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { EMPTY, Subscription, catchError, exhaustMap, firstValueFrom, timer } from 'rxjs';
import { ExperimentosService } from '../../../core/services/experimentos.service';
import { SimApiService } from '../../../core/services/sim-api.service';
import { ClaseUrgencia, ConfiguracionLote, CorridaDoc, CorridaResumen, CrearLoteRequest, EstadoExperimental, GeneradorInstanciaRequest, InstanciaDoc, InstanciaResumen, LoteDoc, LoteResumen, OpcionesOlas, PaginaPedidos, PedidoExperimento, ServicioClase, SolicitudInstancia } from '../../../core/models/experimentos.model';

@Component({selector:'app-experimentos', templateUrl:'./experimentos.component.html',
  styleUrls:['./experimentos.component.scss'], imports:[FormsModule,RouterLink,DatePipe,DecimalPipe,JsonPipe]})
export class ExperimentosComponent implements OnInit, OnDestroy {
  @Input() vista:'configuracion'|'monitor'|'historial' = 'configuracion';
  readonly clases:ClaseUrgencia[]=['muy_baja','baja','media','alta','critica'];
  busy=false; error=''; conectado=false;
  semilla=42; cantidad=500; productos=20; x:number|null=null; y:number|null=null; z=4;
  ocupacion=.65; robots=1; mediaUrgencia=0; desviacionUrgencia=1; plazoBase:number|null=null;
  perfil:'uniforme'|'concentrado'='uniforme';
  instancias:InstanciaResumen[]=[]; elegida=''; instancia:InstanciaDoc|null=null;
  filtroInstancia=''; claseInstancia=''; paginaInstancia=1; readonly tamanoInstancia=50;
  opciones:OpcionesOlas={tamano:25,regla:'original',preparacion_fija:0,preparacion_por_pedido:0,
    limite_ticks:100000,ventana_sin_entregas:null,adaptativa:false,minimo:1,maximo:32,
    objetivo_ticks_por_pedido:15,modelo_preparacion:'fisica'};
  estado:EstadoExperimental|null=null;
  pedidosMonitor:PaginaPedidos|null=null; paginaMonitor=1; filtroMonitor=''; claseMonitor=''; estadoMonitor='';
  eventosMonitor:unknown[]=[]; cursorEventos=0; hayMasEventos=false;
  tipoEvento=''; pedidoEvento=''; olaEvento:number|null=null;
  corridas:CorridaResumen[]=[]; seleccion:{doc:CorridaDoc; pedidos:PaginaPedidos|null; pagina:number}[]=[];
  pagina=0; filtro=''; filtroEstado=''; filtroVersion='';
  lotes:LoteResumen[]=[]; loteSeleccionado=''; lote:LoteDoc|null=null;
  nombreLote=''; tamanosLote='25,50,100'; incluirOlaCompleta=true;
  reglaOriginal=true; reglaUrgentes=true;
  loteError='';
  private loteSub?:Subscription; private rutaSub?:Subscription;
  private loteFetch=false;
  private destruido=false;
  get solicitudesVisibles(){
    const ss=this.instancia?.instancia.solicitudes??[];
    const q=this.filtroInstancia.trim().toLowerCase();
    return ss.filter(s=>(!this.claseInstancia||s.urgencia===this.claseInstancia)&&
      (!q||s.pedido.id_pedido.toLowerCase().includes(q)||s.pedido.id_sku.toLowerCase().includes(q)))
      .slice((this.paginaInstancia-1)*this.tamanoInstancia,this.paginaInstancia*this.tamanoInstancia);
  }
  get totalSolicitudesFiltradas(){
    const ss=this.instancia?.instancia.solicitudes??[];const q=this.filtroInstancia.trim().toLowerCase();
    return ss.filter(s=>(!this.claseInstancia||s.urgencia===this.claseInstancia)&&
      (!q||s.pedido.id_pedido.toLowerCase().includes(q)||s.pedido.id_sku.toLowerCase().includes(q))).length;
  }
  get paginasInstancia(){return Math.max(1,Math.ceil(this.totalSolicitudesFiltradas/this.tamanoInstancia));}
  get filtradas(){return this.corridas.filter(r=>(!this.filtroEstado||r.estado===this.filtroEstado)&&
    (!this.filtroVersion||String(r.version??1)===this.filtroVersion)&&
    (r.id.includes(this.filtro.trim().toLowerCase())||r.instancia_id.includes(this.filtro.trim().toLowerCase())));}
  get paginas(){return Math.max(1,Math.ceil(this.filtradas.length/12));}
  get visibles(){return this.filtradas.slice(this.pagina*12,(this.pagina+1)*12);}
  get tamanosParseados(){
    const tokens=this.tamanosLote.split(',').map(x=>x.trim()).filter(Boolean);
    const n=this.instancia?.instancia.solicitudes.length??0;
    const invalidos=tokens.filter(x=>!/^\d+$/.test(x)||Number(x)<1||Number(x)>n);
    const tamanos=[...new Set(tokens.filter(x=>/^\d+$/.test(x)).map(Number))];
    return {tamanos,invalidos};
  }
  get reglasSeleccionadas(){return [
    ...(this.reglaOriginal?['original']:[]),...(this.reglaUrgentes?['urgentes_primero']:[]),
  ];}
  get configuracionesLotePreview(){
    const tamano=[...this.tamanosParseados.tamanos];
    const total=this.instancia?.instancia.solicitudes.length??0;
    if(this.incluirOlaCompleta&&total>0)tamano.push(total);
    const sizes=[...new Set(tamano)];
    const configuraciones:{tamano:number;regla:string}[]=[];
    for(const n of sizes)for(const regla of this.reglasSeleccionadas)configuraciones.push({tamano:n,regla});
    return configuraciones;
  }
  get errorConfiguracionLote(){
    const n=this.instancia?.instancia.solicitudes.length??0;
    if(!this.instancia)return 'Genera o recupera una instancia antes de comparar.';
    if(this.tamanosParseados.invalidos.length)return `Tamaños inválidos: ${this.tamanosParseados.invalidos.join(', ')}. Usa enteros entre 1 y ${n}.`;
    if(!this.configuracionesLotePreview.length)return 'Elige al menos un tamaño y una regla.';
    if(this.configuracionesLotePreview.length>24)return 'El lote admite como máximo 24 combinaciones. Reduce los tamaños o desactiva una regla.';
    return '';
  }
  get loteEnCurso(){return !!this.lote&&this.loteActivo(this.lote.estado);}
  private sub?:Subscription;
  private runId='';
  private refreshSignature='';
  private monitorFetch=false;
  constructor(private api:ExperimentosService, private control:SimApiService, private router:Router, private route:ActivatedRoute) {}
  ngOnInit() {
    if(this.vista==='configuracion') void this.actualizarInstancias();
    if(this.vista==='historial'){
      void this.inicializarHistorial();
    }
    if(this.vista==='configuracion'||this.vista==='historial'){
      void this.actualizarLotes();
      this.loteSub=timer(1000,1000).pipe(exhaustMap(()=>{
        if(!this.loteSeleccionado||!this.lote||!this.loteActivo(this.lote.estado))return EMPTY;
        return this.api.lote(this.loteSeleccionado).pipe(catchError(e=>{this.loteError=this.mensaje(e);return EMPTY;}));
      })).subscribe(doc=>this.recibirLote(doc));
    }
    if(this.vista==='monitor') this.sub=timer(0,1000).pipe(exhaustMap(()=>this.api.estado().pipe(
      catchError(e=>{this.conectado=false;this.error=this.mensaje(e);return EMPTY;})
    ))).subscribe(s=>{
      this.estado=s;this.conectado=true;this.error='';
      const id=s.corrida?.id??'';
      if(id&&id!==this.runId){this.runId=id;this.paginaMonitor=1;this.cursorEventos=0;this.eventosMonitor=[];this.refreshSignature='';}
      if(id&&s.experimento){
        const firma=s.status==='RUNNING'?`${id}:${Math.floor(s.experimento.tiempo/50)}`:
          `${id}:${s.status}:${s.experimento.tiempo}`;
        if(firma!==this.refreshSignature)void this.refrescarMonitor(firma);
      }
    });
  }
  ngOnDestroy(){this.destruido=true;this.sub?.unsubscribe();this.loteSub?.unsubscribe();this.rutaSub?.unsubscribe();}
  mensaje(e:unknown):string {
    const detail=(e as {error?:{detail?:unknown}})?.error?.detail;
    const status=(e as {status?:number})?.status;
    if(status===409)return typeof detail==='string'?detail:'Ya hay otro lote activo. Espera a que termine o cancélalo antes de iniciar otro.';
    if(Array.isArray(detail))return detail.map(x=>(x as {msg?:string}).msg??'Entrada inválida').join(' ');
    return typeof detail==='string'?detail:'No se pudo completar la operación. Comprueba la conexión con el simulador e inténtalo nuevamente.';
  }
  nombreClase(c:string){return ({muy_baja:'Muy baja',baja:'Baja',media:'Media',alta:'Alta',critica:'Crítica'} as Record<string,string>)[c]??c;}
  cuentaClase(c:string){return this.instancia?.instancia.solicitudes.filter(s=>s.urgencia===c).length??0;}
  esperadaClase(c:ClaseUrgencia){return this.instancia?.instancia.parametros?.frecuencias_esperadas?.[c]??0;}
  async accion(tarea:()=>Promise<void>) {
    if(this.busy)return;this.busy=true;this.error='';
    try{await tarea();}catch(e){this.error=this.mensaje(e);}finally{this.busy=false;}
  }
  async actualizarInstancias(){await this.accion(async()=>{this.instancias=(await firstValueFrom(this.api.instancias())).instancias;});}
  async generar(){await this.accion(async()=>{
    const request:GeneradorInstanciaRequest={version:2,semilla:this.semilla,cantidad:this.cantidad,productos:this.productos,
      x:this.x,y:this.y,z:this.z,ocupacion:this.ocupacion,robots:this.robots,media_urgencia:this.mediaUrgencia,
      desviacion_urgencia:this.desviacionUrgencia,plazo_base:this.plazoBase,perfil:this.perfil};
    this.instancia=await firstValueFrom(this.api.generar(request));this.elegida=this.instancia.id;this.tamanosPorDefecto();
    this.opciones={...this.opciones,modelo_preparacion:'fisica',tamano:25,regla:'original',
      preparacion_fija:0,preparacion_por_pedido:0,limite_ticks:100000,adaptativa:false};
    this.paginaInstancia=1;this.instancias=(await firstValueFrom(this.api.instancias())).instancias;
  });}
  async recuperar(){await this.accion(async()=>{this.instancia=await firstValueFrom(this.api.instancia(this.elegida));this.paginaInstancia=1;
    this.tamanosPorDefecto();
    if(this.instancia.instancia.version>=2){this.opciones={...this.opciones,modelo_preparacion:'fisica',preparacion_fija:0,preparacion_por_pedido:0,limite_ticks:100000,adaptativa:false};}
    else{this.opciones={...this.opciones,modelo_preparacion:'sintetica',tamano:4,regla:'urgentes_primero',preparacion_fija:2,preparacion_por_pedido:1,limite_ticks:1000,adaptativa:false};}
  });}
  async iniciar(){if(!this.instancia)return;await this.accion(async()=>{
    await firstValueFrom(this.api.cargar(this.instancia!.id,this.opciones));
    await firstValueFrom(this.control.setSpeed(1000));
    await firstValueFrom(this.control.play());
    await this.router.navigate(['/monitor']);
  });}
  async controlar(tipo:'play'|'pause'|'reset'){await this.accion(async()=>{
    await firstValueFrom(this.control[tipo]());this.estado=await firstValueFrom(this.api.estado());
  });}
  async actualizarPedidosMonitor(){
    if(!this.runId)return;
    await this.accion(async()=>{this.pedidosMonitor=await firstValueFrom(this.api.pedidos(this.runId,this.paginaMonitor,50,this.claseMonitor,this.estadoMonitor,this.filtroMonitor));
      const pages=Math.max(1,Math.ceil(this.pedidosMonitor.total/50));
      if(this.paginaMonitor>pages){this.paginaMonitor=pages;this.pedidosMonitor=await firstValueFrom(this.api.pedidos(this.runId,pages,50,this.claseMonitor,this.estadoMonitor,this.filtroMonitor));}
    });
  }
  private async refrescarMonitor(firma:string){
    if(!this.runId||this.monitorFetch||this.busy)return;
    this.monitorFetch=true;
    try{
      const page=await firstValueFrom(this.api.pedidos(this.runId,this.paginaMonitor,50,this.claseMonitor,this.estadoMonitor,this.filtroMonitor));
      const paginas=Math.max(1,Math.ceil(page.total/50));
      this.paginaMonitor=Math.min(this.paginaMonitor,paginas);
      this.pedidosMonitor=this.paginaMonitor===page.pagina?page:
        await firstValueFrom(this.api.pedidos(this.runId,this.paginaMonitor,50,this.claseMonitor,this.estadoMonitor,this.filtroMonitor));
      this.refreshSignature=firma;
    }catch(e){this.error=this.mensaje(e);}finally{this.monitorFetch=false;}
  }
  async paginaMonitorA(delta:number){this.paginaMonitor=Math.max(1,Math.min(Math.ceil((this.pedidosMonitor?.total??0)/50),this.paginaMonitor+delta));await this.actualizarPedidosMonitor();}
  async cargarEventos(){if(!this.runId)return;await this.accion(async()=>{
    const page=await firstValueFrom(this.api.eventos(this.runId,this.cursorEventos,100,this.tipoEvento,this.pedidoEvento,this.olaEvento));
    this.eventosMonitor=[...this.eventosMonitor,...page.items];this.cursorEventos=page.siguiente;this.hayMasEventos=page.hay_mas;
  });}
  resetearEventos(){this.cursorEventos=0;this.eventosMonitor=[];this.hayMasEventos=false;}
  async actualizarHistorial(){await this.accion(async()=>{
    this.corridas=(await firstValueFrom(this.api.historial())).corridas;this.pagina=Math.min(this.pagina,this.paginas-1);
    this.seleccion=await Promise.all(this.seleccion.map(async d=>({doc:await firstValueFrom(this.api.resultado(d.doc.resultado.id)),pedidos:d.pedidos,pagina:d.pagina})));
  });}
  private async inicializarHistorial(){
    await this.actualizarHistorial();
    if(this.destruido)return;
    this.rutaSub=this.route.queryParamMap.subscribe(params=>{
      const corridaId=params.get('corridaId');
      if(corridaId&&!this.tiene(corridaId)){this.seleccion=[];void this.seleccionar(corridaId);}
    });
  }
  private tamanosPorDefecto(){
    const n=this.instancia?.instancia.solicitudes.length??0;
    if(n>0)this.tamanosLote=[...new Set([25,50,100].map(t=>Math.min(t,n)))].join(',');
  }
  private loteActivo(estado:string){return ['lista','ejecutando','cancelando'].includes(estado);}
  async actualizarLotes(){
    try{
      this.lotes=(await firstValueFrom(this.api.lotes())).lotes
        .slice().sort((a,b)=>b.creada.localeCompare(a.creada));
      const vigente=this.lotes.find(l=>this.loteActivo(l.estado));
      const existente=this.lotes.find(l=>l.id===this.loteSeleccionado);
      const elegido=vigente?.id??existente?.id??this.lotes[0]?.id??'';
      this.loteSeleccionado=elegido;
      if(elegido)await this.cargarLote(elegido);
    }catch(e){this.loteError=this.mensaje(e);}
  }
  async seleccionarLote(id:string){
    this.loteSeleccionado=id;this.lote=null;
    if(id)await this.cargarLote(id);
  }
  private async cargarLote(id:string){
    if(this.loteFetch)return;
    this.loteFetch=true;
    try{this.recibirLote(await firstValueFrom(this.api.lote(id)));this.loteError='';}
    catch(e){this.loteError=this.mensaje(e);}
    finally{this.loteFetch=false;}
  }
  private recibirLote(doc:LoteDoc){
    this.lote=doc;this.loteSeleccionado=doc.id;
    const i=this.lotes.findIndex(l=>l.id===doc.id);
    if(i>=0)this.lotes[i]={...this.lotes[i],...doc};
  }
  async crearLote(){
    if(this.busy||this.errorConfiguracionLote||!this.instancia)return;
    this.loteError='';
    const request:CrearLoteRequest={instancia_id:this.instancia.id,nombre:this.nombreLote.trim()||null,
      configuraciones:this.configuracionesLotePreview,limite_ticks:this.opciones.limite_ticks,
      ventana_sin_entregas:this.opciones.ventana_sin_entregas};
    this.busy=true;
    try{
      const creado=await firstValueFrom(this.api.crearLote(request));
      this.recibirLote(creado);await this.actualizarLotes();
    }catch(e){this.loteError=this.mensaje(e);}
    finally{this.busy=false;}
  }
  async cancelarLote(){
    if(!this.loteSeleccionado||!this.lote||!this.loteActivo(this.lote.estado))return;
    this.loteError='';
    try{this.recibirLote(await firstValueFrom(this.api.cancelarLote(this.loteSeleccionado)));}
    catch(e){this.loteError=this.mensaje(e);}
  }
  urlExportarLote(id:string){return this.api.exportarLoteUrl(id);}
  async abrirCorrida(corridaId:string){
    await this.router.navigate(['/reportes'],{queryParams:{corridaId}});
  }
  etiquetaLote(estado:string){return ({lista:'En cola',ejecutando:'Ejecutando',cancelando:'Cancelando',completa:'Completo',cancelada:'Cancelado',interrumpida:'Interrumpido',error:'Error',pendiente:'Pendiente',registrada:'Registrada',omitida:'Omitida'} as Record<string,string>)[estado]??estado;}
  tiempoComparacion(c:ConfiguracionLote){return c.resultado?.estado==='completa'&&c.resultado.tiempo_finalizacion!==null?`${c.resultado.tiempo_finalizacion} ticks`:'—';}
  porcentaje(valor:number|null|undefined){return valor==null?'—':`${(valor<=1?valor*100:valor).toFixed(1)}%`;}
  tiene(id:string){return this.seleccion.some(d=>d.doc.resultado.id===id);}
  async seleccionar(id:string){
    if(this.tiene(id)){this.seleccion=this.seleccion.filter(d=>d.doc.resultado.id!==id);return;}
    if(this.seleccion.length===2)return;
    await this.accion(async()=>{const doc=await firstValueFrom(this.api.resultado(id));
      const pedidos=await firstValueFrom(this.api.pedidos(id,1,50));this.seleccion=[...this.seleccion,{doc,pedidos,pagina:1}];});
  }
  async paginaSeleccionada(runId:string,delta:number){const i=this.seleccion.findIndex(d=>d.doc.resultado.id===runId);if(i<0)return;
    const current=this.seleccion[i];const pages=Math.max(1,Math.ceil((current.pedidos?.total??0)/50));const pagina=Math.max(1,Math.min(pages,current.pagina+delta));
    await this.accion(async()=>{const pedidos=await firstValueFrom(this.api.pedidos(runId,pagina,50));this.seleccion[i]={...current,pagina,pedidos};});
  }
  get terminal(){return ['completa','horizonte_agotado','sin_entregas','error','interrumpida','inviable_preparacion'].includes(this.estado?.corrida?.estado??'');}
  get diferentes(){return this.seleccion.length===2&&this.seleccion[0].doc.entrada.instancia_id!==this.seleccion[1].doc.entrada.instancia_id;}
  etiqueta(s:string){return ({lista:'Lista',ejecutando:'En ejecución',pausada:'Pausada',completa:'Completa',horizonte_agotado:'Horizonte agotado',inviable_preparacion:'Preparación inviable',sin_entregas:'Sin entregas',error:'Error',interrumpida:'Interrumpida',preparando:'Preparando',liberada:'Liberada',original:'Orden original',urgentes_primero:'Prioridad de urgencia',fisica:'Preparación física',sintetica:'Preparación sintética',legacy:'Preparación sintética'} as Record<string,string>)[s]??s;}
  estadoPedido(p:PedidoExperimento){return p.entrega!==null?'Entregado':p.asignacion!==null?'En atención':p.liberacion!==null?'Liberado':'Por liberar';}
  cumplimiento(s?:ServicioClase){const n=s?.cumplimiento_plazo??(s?.cumplimiento==null?null:s.cumplimiento*100)??
    (s&&s.con_plazo?s.a_tiempo/s.con_plazo*100:null);return n==null?'—':`${n.toFixed(1)}%`;}
  tiempoMedio(s?:ServicioClase){return s?.tiempo_promedio_ticks??s?.tiempo_medio_entrega??'—';}
  sumEntregados(s?:Partial<Record<ClaseUrgencia,ServicioClase>>){return Object.values(s??{}).reduce((n,m)=>n+(m?.entregados??0),0);}
  posicion(p?:{x:number;y:number;z:number}|null){return p?`(${p.x}, ${p.y}, ${p.z})`:'—';}
  cantidadPendiente(p?:number|unknown[]){return Array.isArray(p)?p.length:(p??'—');}
  trackSolicitud(_i:number,s:SolicitudInstancia){return s.pedido.id_pedido;}
}
