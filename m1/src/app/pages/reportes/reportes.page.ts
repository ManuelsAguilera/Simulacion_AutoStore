import { Component, OnDestroy, OnInit } from '@angular/core';
import { Subscription } from 'rxjs';
import { AlertController } from '@ionic/angular/standalone';
import { BusClientService, BusState } from '../../core/services/bus-client.service';
import { SimApiService } from '../../core/services/sim-api.service';
import { BusStripComponent } from '../../shared/components/bus-strip/bus-strip.component';
import { ExperimentosComponent } from '../../shared/components/experimentos/experimentos.component';

@Component({
  selector: 'app-reportes',
  templateUrl: './reportes.page.html',
  imports: [BusStripComponent, ExperimentosComponent],
})
export class ReportesPage implements OnInit, OnDestroy {
  bus: BusState | null = null;
  private sub!: Subscription;

  constructor(
    private busService: BusClientService,
    private simApi: SimApiService,
    private alertCtrl: AlertController,
  ) {}

  ngOnInit(): void { this.sub = this.busService.bus$.subscribe(s => (this.bus = s)); }
  ngOnDestroy(): void { this.sub?.unsubscribe(); }

  async downloadComparativo(): Promise<void> {
    this.simApi.getReportStatus().subscribe(async (status) => {
      if (status.finished_runs < status.needed_for_comparativo) {
        const faltan = status.needed_for_comparativo - status.finished_runs;
        const alert = await this.alertCtrl.create({
          header: 'Simulaciones insuficientes',
          message: `Faltan ${faltan} simulación(es) terminada(s) para generar el reporte comparativo.`,
          buttons: ['OK'],
        });
        await alert.present();
      } else {
        this.simApi.downloadComparativo();
      }
    });
  }

  exportSesion(): void {
    this.simApi.exportSesion();
  }

  exportM3(): void {
    this.simApi.exportM3();
  }
}
