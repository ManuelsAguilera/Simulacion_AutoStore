import { Component, ElementRef, OnDestroy, OnInit, ViewChild } from '@angular/core';
import { Router } from '@angular/router';
import { Subscription } from 'rxjs';
import { AlertController, ToastController } from '@ionic/angular/standalone';
import { BusClientService, BusState, FORUS_DEFAULTS, DEMO1_CONFIG, DEMO2_CONFIG } from '../../core/services/bus-client.service';
import { SimApiService } from '../../core/services/sim-api.service';
import { SimMode, PickingPolicy } from '../../core/enums/sim.enums';
import { FileLoaderComponent } from '../../shared/components/file-loader/file-loader.component';
import { PolicySelectorComponent } from '../../shared/components/policy-selector/policy-selector.component';
import { ExperimentosComponent } from '../../shared/components/experimentos/experimentos.component';

@Component({
  selector: 'app-config',
  templateUrl: './config.page.html',
  styleUrls: ['./config.page.scss'],
  imports: [FileLoaderComponent, PolicySelectorComponent, ExperimentosComponent],
})
export class ConfigPage implements OnInit, OnDestroy {
  bus: BusState | null = null;
  demoCargado: 'demo1' | 'demo2' | null = null;
  private sub!: Subscription;

  readonly SimMode       = SimMode;
  readonly PickingPolicy = PickingPolicy;

  customPolicies: { name: string; label: string }[] = [];
  policyUploadEstado: 'ok' | 'error' | null = null;
  policyUploadMsg = '';

  private csvErrorsByField: Record<'archivoOla' | 'archivoReposicion', string[]> = {
    archivoOla: [],
    archivoReposicion: [],
  };

  private csvFileNameByField: Record<'archivoOla' | 'archivoReposicion', string> = {
    archivoOla: '',
    archivoReposicion: '',
  };

  constructor(
    private busService: BusClientService,
    private simApi: SimApiService,
    private router: Router,
    private alertCtrl: AlertController,
    private toastCtrl: ToastController,
  ) {}

  ngOnInit(): void {
    this.sub = this.busService.bus$.subscribe(s => (this.bus = s));
  }

  // ── Slider helpers ──────────────────────────────────────────────────────
  setX(e: Event)   { this.busService.setField({ grid: { ...this.busService.state.grid, x: +(<HTMLInputElement>e.target).value } }); }
  setY(e: Event)   { this.busService.setField({ grid: { ...this.busService.state.grid, y: +(<HTMLInputElement>e.target).value } }); }
  setZ(e: Event)   { this.busService.setField({ grid: { ...this.busService.state.grid, z: +(<HTMLInputElement>e.target).value } }); }
  setR(e: Event)   { this.busService.setField({ numRobots:        +(<HTMLInputElement>e.target).value }); }
  setOcc(e: Event) { this.busService.setField({ ocupacionInicial: +(<HTMLInputElement>e.target).value }); }

  // Robots 1×2 con orientación fija: al cambiar un conteo, numRobots = suma.
  private setOrientacion(parcial: { robotsNorte?: number; robotsEste?: number; robotsOeste?: number }) {
    const s = this.busService.state;
    const n = parcial.robotsNorte ?? s.robotsNorte;
    const e = parcial.robotsEste  ?? s.robotsEste;
    const o = parcial.robotsOeste ?? s.robotsOeste;
    this.busService.setField({ ...parcial, numRobots: n + e + o });
  }
  setRN(e: Event) { this.setOrientacion({ robotsNorte: +(<HTMLInputElement>e.target).value }); }
  setRE(e: Event) { this.setOrientacion({ robotsEste:  +(<HTMLInputElement>e.target).value }); }
  setRO(e: Event) { this.setOrientacion({ robotsOeste: +(<HTMLInputElement>e.target).value }); }

  setNombre(e: Event)  { this.busService.setField({ nombreEjecucion: (<HTMLInputElement>e.target).value }); }
  setSemilla(e: Event) { this.busService.setField({ semilla: +(<HTMLInputElement>e.target).value || 0 }); }
  randomSemilla()      { this.busService.setField({ semilla: Math.floor(Math.random() * 9e7) + 1e7 }); }

  setMode(m: SimMode)        { this.busService.setMode(m); }
  restaurarForus()            { this.busService.restaurarForus(); }

  readonly DEMO1 = DEMO1_CONFIG;
  readonly DEMO2 = DEMO2_CONFIG;

  cargarDemo1() { this.demoCargado = 'demo1'; this.csvFileNameByField.archivoOla = 'ola_demo1.csv'; this.busService.cargarDemo(DEMO1_CONFIG); }
  cargarDemo2() { this.demoCargado = 'demo2'; this.csvFileNameByField.archivoOla = 'ola_demo2.csv'; this.busService.cargarDemo(DEMO2_CONFIG); }

  onPolicyChange(policy: string): void {
    this.busService.setPolicy(policy);
    const label =
      policy === PickingPolicy.FIFO ? 'FIFO' :
      policy === PickingPolicy.PRIORITY_POSITION ? 'Prioridad Posición' :
      policy;
    this.toastCtrl.create({
      message: `Política cambiada a ${label}`,
      duration: 2000,
      position: 'bottom',
      color: 'success',
    }).then(t => t.present());
  }

  @ViewChild('policyInput') policyInputRef!: ElementRef<HTMLInputElement>;

  onLoadExternal(): void {
    this.policyInputRef?.nativeElement?.click();
  }

  onPolicyFileSelected(event: Event): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    if (!file) return;

    this.policyUploadEstado = null;
    this.policyUploadMsg = '';

    this.simApi.uploadPolicy(file).subscribe({
      next: (res) => {
        const label = this.parseFileName(file.name);
        this.policyUploadEstado = 'ok';
        this.policyUploadMsg = `Política ${file.name} ha sido correctamente cargada`;
        const name = res.policy_name;
        if (!this.customPolicies.some(p => p.name === name)) {
          this.customPolicies.push({ name, label });
        }
        this.busService.setPolicy(name);
      },
      error: (err) => {
        this.policyUploadEstado = 'error';
        this.policyUploadMsg = err.error?.detail ?? 'Error al cargar la política';
      },
    });
    input.value = '';
  }

  private parseFileName(filename: string): string {
    return filename
      .replace(/\.py$/i, '')
      .replace(/[_\-]/g, ' ')
      .replace(/([a-z])([A-Z])/g, '$1 $2')
      .replace(/\s+/g, ' ')
      .trim()
      .split(' ')
      .map(w => w.charAt(0).toUpperCase() + w.slice(1))
      .join(' ');
  }

  // ── Carga real de CSV (T-29) ────────────────────────────────────────────
  onCsvFileSelected(event: Event): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    if (!file) return;

    const tipo = this.csvTipo;
    const campo = this.csvCampo;
    this.csvFileNameByField[campo] = file.name;

    this.simApi.uploadCsv(file, tipo).subscribe({
      next: (res) => {
        this.csvErrorsByField[campo] = res.errors.map(e =>
          e.row > 0 ? `Fila ${e.row} - ${e.column}: ${e.reason}` : `${e.column}: ${e.reason}`
        );
        this.busService.setField({ [campo]: res.valid ? 'valido' : 'errores' } as Partial<BusState>);
      },
      error: () => {
        this.csvErrorsByField[campo] = ['No se pudo conectar con el backend para validar el archivo.'];
        this.busService.setField({ [campo]: 'errores' } as Partial<BusState>);
      },
    });
  }

  get csvErrors(): string[] {
    return this.csvErrorsByField[this.csvCampo];
  }

  get csvTipo(): 'ola'|'reposicion' {
    return this.bus?.mode === SimMode.DIURNO ? 'ola' : 'reposicion';
  }

  get csvCampo(): 'archivoOla'|'archivoReposicion' {
    return this.bus?.mode === SimMode.DIURNO ? 'archivoOla' : 'archivoReposicion';
  }

  get csvEstado(): 'valido'|'errores'|'no_cargado' {
    return (this.bus?.[this.csvCampo]) ?? 'no_cargado';
  }

  get csvCols(): string[] {
    return this.csvTipo === 'ola'
      ? ['id_pedido','id_sku','cantidad','destino']
      : ['id_caja','id_sku','cantidad'];
  }

  get csvArchivoNombre(): string {
    const nombreReal = this.csvFileNameByField[this.csvCampo];
    return nombreReal || (this.csvTipo === 'ola' ? 'ola.csv' : 'reposicion.csv');
  }

  // ── Computed ────────────────────────────────────────────────────────────
  get capacidad(): number {
    const g = this.bus?.grid ?? { x:12, y:10, z:5 };
    return g.x * g.y * g.z;
  }

  get paramError(): boolean { return this.capacidad > 8000; }

  get csvListo(): boolean {
    return this.csvEstado === 'valido';
  }

  get puedeIniciar(): boolean {
    return !this.paramError && this.csvListo && this.bus?.falloSistema !== 'motor';
  }

  get bloqueo(): string | null {
    if (this.paramError) return 'Capacidad de grilla fuera del máximo permitido.';
    if (!this.csvListo)  return `${this.csvArchivoNombre} no está cargado o contiene filas inválidas.`;
    if (this.bus?.falloSistema === 'motor') return 'El Motor de Simulación no responde.';
    return null;
  }

  get vramPct(): number {
    return Math.min(98, 38 + (this.capacidad / 8000) * 55);
  }

  get isForus(): boolean {
    const b = this.bus;
    if (!b) return false;
    return b.grid.x === FORUS_DEFAULTS.grid.x && b.grid.y === FORUS_DEFAULTS.grid.y &&
           b.grid.z === FORUS_DEFAULTS.grid.z && b.numRobots === FORUS_DEFAULTS.numRobots &&
           b.ocupacionInicial === FORUS_DEFAULTS.ocupacionInicial;
  }

  get configJson(): string {
    const b = this.bus;
    if (!b) return '{}';
    return JSON.stringify({
      nombre_ejecucion: b.nombreEjecucion,
      grilla: b.grid,
      robots_activos: b.numRobots,
      robots_orientacion: { norte: b.robotsNorte, este: b.robotsEste, oeste: b.robotsOeste },
      ocupacion_inicial_pct: b.ocupacionInicial,
      semilla: b.semilla,
      modo_turno: b.mode === SimMode.DIURNO ? 'diurno_picking' : 'nocturno_reposicion',
      archivo_datos: this.csvFileNameByField[this.csvCampo] || this.csvArchivoNombre,
      estado_archivo: this.csvEstado,
      politica_picking: b.policy.toLowerCase(),
      pedidos_demandados: b.pedidosDemandados,
      velocidad: `${b.velocidad}x`,
    }, null, 2);
  }

  // ── Fallos sistema ──────────────────────────────────────────────────────
  setFallo(f: 'motor'|'omniverse'|null): void { this.busService.setField({ falloSistema: f }); }
  clearFallo(): void { this.busService.setField({ falloSistema: null }); }
  goHeadless(): void { this.busService.setField({ falloSistema: null, omniverse: 'headless' }); }

  // ── Start ────────────────────────────────────────────────────────────────
  onCargarConfig(event: Event): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      input.value = '';
      try {
        const json = JSON.parse(reader.result as string);
        if (typeof json.x !== 'number' || typeof json.y !== 'number' || typeof json.z !== 'number'
            || typeof json.robots !== 'number' || typeof json.ocupacion !== 'number') {
          alert('El archivo debe contener x, y, z, robots y ocupacion.');
          return;
        }
        const s = this.busService.state;
        this.busService.applyConfig({
          x: json.x, y: json.y, z: json.z,
          numRobots: json.robots,
          occupancyPct: Math.round(json.ocupacion * 100),
          mode: s.mode,
          policy: s.policy,
          sessionName: s.nombreEjecucion,
          semilla: s.semilla,
          pedidosDemandados: s.pedidosDemandados,
          robotsNorte: s.robotsNorte,
          robotsEste: s.robotsEste,
          robotsOeste: s.robotsOeste,
        });
      } catch {
        alert('El archivo no es un JSON válido.');
      }
    };
    reader.readAsText(file);
  }

  guardarConfig(): void {
    const s = this.busService.state;
    const data = {
      x: s.grid.x,
      y: s.grid.y,
      z: s.grid.z,
      robots: s.numRobots,
      ocupacion: s.ocupacionInicial / 100,
    };
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'config.json';
    a.click();
    URL.revokeObjectURL(url);
  }

  async iniciar(): Promise<void> {
    if (!this.puedeIniciar) return;
    const alert = await this.alertCtrl.create({
      header: '¿Iniciar simulación?',
      message: 'Se enviará la configuración al Motor y comenzará la ejecución.',
      buttons: [
        { text: 'Cancelar', role: 'cancel' },
        { text: 'Iniciar', role: 'confirm' },
      ],
    });
    await alert.present();
    const { role } = await alert.onDidDismiss();
    if (role !== 'confirm') return;
    this.busService.startSimulation();
    this.router.navigate(['/monitor']);
  }

  ngOnDestroy(): void { this.sub?.unsubscribe(); }
}
