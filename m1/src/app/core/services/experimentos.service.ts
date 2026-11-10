import { Injectable } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { environment } from '../../../environments/environment';
import { CorridaDoc, CorridaResumen, CrearLoteRequest, EstadoExperimental, GeneradorInstanciaRequest, InstanciaDoc, InstanciaResumen, LoteDoc, LoteResumen, OpcionesOlas, PaginaEventos, PaginaPedidos } from '../models/experimentos.model';

@Injectable({providedIn:'root'})
export class ExperimentosService {
  private base = environment.apiUrl;
  constructor(private http:HttpClient) {}
  instancias() { return this.http.get<{instancias:InstanciaResumen[]}>(`${this.base}/experiments/instances`); }
  generar(request:GeneradorInstanciaRequest) {
    return this.http.post<InstanciaDoc>(`${this.base}/experiments/instances/generate`, request);
  }
  instancia(id:string) { return this.http.get<InstanciaDoc>(`${this.base}/experiments/instances/${id}`); }
  cargar(id:string, opciones:OpcionesOlas) {
    return this.http.post(`${this.base}/experiments/instances/${id}/load`,opciones);
  }
  estado() { return this.http.get<EstadoExperimental>(`${this.base}/snapshot?compacto=true`); }
  historial() { return this.http.get<{corridas:CorridaResumen[]}>(`${this.base}/experiments/runs`); }
  resultado(id:string) { return this.http.get<CorridaDoc>(`${this.base}/experiments/runs/${id}?compacto=true`); }
  pedidos(id:string, pagina=1, tamano=50, urgencia='', estado='', busqueda='') {
    let params = new HttpParams().set('pagina',pagina).set('tamano',tamano);
    if(urgencia) params=params.set('urgencia',urgencia);
    if(estado) params=params.set('estado',estado);
    if(busqueda.trim()) params=params.set('busqueda',busqueda.trim());
    return this.http.get<PaginaPedidos>(`${this.base}/experiments/runs/${id}/pedidos`,{params});
  }
  eventos(id:string, desde=0, limite=100, tipo='', idPedido='', ola:number|null=null) {
    let params=new HttpParams().set('desde',desde).set('limite',limite);
    if(tipo)params=params.set('tipo',tipo);
    if(idPedido.trim())params=params.set('id_pedido',idPedido.trim());
    if(ola!==null)params=params.set('ola',ola);
    return this.http.get<PaginaEventos>(`${this.base}/experiments/runs/${id}/eventos`,{params});
  }
  crearLote(body:CrearLoteRequest) {
    return this.http.post<LoteDoc>(`${this.base}/experiments/batches`,body);
  }
  lotes() {
    return this.http.get<{lotes:LoteResumen[]}>(`${this.base}/experiments/batches`);
  }
  lote(id:string) {
    return this.http.get<LoteDoc>(`${this.base}/experiments/batches/${encodeURIComponent(id)}`);
  }
  cancelarLote(id:string) {
    return this.http.post<LoteDoc>(`${this.base}/experiments/batches/${encodeURIComponent(id)}/cancel`,{});
  }
  exportarLoteUrl(id:string) {
    return `${this.base}/experiments/batches/${encodeURIComponent(id)}/export`;
  }
}
