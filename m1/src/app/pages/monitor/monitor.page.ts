import { Component, OnDestroy, OnInit } from '@angular/core';
import { Subscription } from 'rxjs';
import { AlertController } from '@ionic/angular/standalone';
import { BusClientService, BusState } from '../../core/services/bus-client.service';
import { BusStripComponent } from '../../shared/components/bus-strip/bus-strip.component';
import { ControlBarComponent } from '../../shared/components/control-bar/control-bar.component';
import { RobotGridComponent } from '../../shared/components/robot-grid/robot-grid.component';
import { KpiSidebarComponent } from '../../shared/components/kpi-sidebar/kpi-sidebar.component';
import { ExperimentosComponent } from '../../shared/components/experimentos/experimentos.component';

type Vista = '2d' | '3d';

@Component({
  selector: 'app-monitor',
  templateUrl: './monitor.page.html',
  styleUrls: ['./monitor.page.scss'],
  imports: [BusStripComponent, ControlBarComponent, RobotGridComponent, KpiSidebarComponent, ExperimentosComponent],
})
export class MonitorPage implements OnInit, OnDestroy {
  bus: BusState | null = null;
  private sub!: Subscription;

  vista: Vista = '2d';

  constructor(
    private busService: BusClientService,
    private alertCtrl: AlertController,
  ) {}

  ngOnInit(): void {
    this.sub = this.busService.bus$.subscribe(s => (this.bus = s));
  }

  setVista(v: Vista): void {
    this.vista = v;
  }

  onPlay(): void  { this.busService.setRunning(true); }
  onPause(): void { this.busService.setRunning(false); }
  async onReset(): Promise<void> {
    const alert = await this.alertCtrl.create({
      header: '¿Reiniciar simulación?',
      message: 'Se perderá el progreso actual.',
      buttons: [
        { text: 'Cancelar', role: 'cancel' },
        { text: 'Reiniciar', role: 'confirm' },
      ],
    });
    await alert.present();
    const { role } = await alert.onDidDismiss();
    if (role !== 'confirm') return;
    this.busService.reset();
  }
  onSpeedChange(s: 1|2|5): void { this.busService.setSpeed(s); }

  ngOnDestroy(): void { this.sub?.unsubscribe(); }
}
