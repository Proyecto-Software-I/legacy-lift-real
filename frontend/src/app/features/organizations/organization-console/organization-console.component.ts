import { Component, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ApiService, Organization, OrganizationMember, OrganizationReport, OrganizationUsage, AuditEntry } from '../../../core/api/api.service';
import { WorkspaceFacade } from '../../../core/state/workspace.facade';
import { IconComponent, DialogComponent } from '../../../shared/ui';

@Component({
  selector: 'll-organization-console', standalone: true,
  imports: [CommonModule, FormsModule, IconComponent, DialogComponent],
  templateUrl: './organization-console.component.html',
  styleUrl: './organization-console.component.css'
})
export class OrganizationConsoleComponent {
  readonly vm = inject(WorkspaceFacade);
  private readonly api = inject(ApiService);
  readonly open = signal(false);
  readonly busy = signal(false);
  readonly error = signal('');
  readonly info = signal('');
  readonly orgs = signal<Organization[]>([]);
  readonly selectedId = signal('');
  readonly members = signal<OrganizationMember[]>([]);
  readonly report = signal<OrganizationReport | null>(null);
  readonly usage = signal<OrganizationUsage | null>(null);
  readonly audit = signal<AuditEntry[]>([]);
  readonly invitationCode = signal('');
  newName = '';
  inviteEmail = '';
  inviteRole: 'viewer' | 'developer' | 'admin' = 'developer';
  acceptCode = '';

  get selectedOrg(): Organization | undefined { return this.orgs().find(o => o.id === this.selectedId()); }
  get canManage(): boolean { return this.selectedOrg?.role === 'admin' || this.selectedOrg?.role === 'owner'; }
  show(): void { this.open.set(true); this.refresh(); }
  close(): void { this.open.set(false); this.invitationCode.set(''); }
  private failure(err: any): void {
    this.busy.set(false); this.error.set(err?.error?.detail || err?.message || 'No se pudo completar la operación');
  }
  refresh(): void {
    this.api.organizations().subscribe({next: rows => {
      this.orgs.set(rows);
      if (!rows.some(o => o.id === this.selectedId())) this.selectedId.set(rows[0]?.id || '');
      this.load(); this.vm.loadOrganizations();
    }, error: err => this.failure(err)});
  }
  select(id: string): void { this.selectedId.set(id); this.load(); }
  load(): void {
    const id = this.selectedId();
    this.members.set([]); this.report.set(null); this.usage.set(null); this.audit.set([]);
    if (!id) return;
    this.api.organizationReport(id).subscribe({
      next: value => { if(this.selectedId()===id)this.report.set(value); },
      error: err => this.failure(err)
    });
    this.api.organizationUsage(id).subscribe({
      next: value => { if(this.selectedId()===id)this.usage.set(value); },
      error: err => this.failure(err)
    });
    if (this.canManage) {
      this.api.organizationMembers(id).subscribe({
        next:rows=>{if(this.selectedId()===id)this.members.set(rows);},
        error:err=>this.failure(err)
      });
      this.api.organizationAudit(id).subscribe({
        next:rows=>{if(this.selectedId()===id)this.audit.set(rows);},
        error:err=>this.failure(err)
      });
    }
  }
  create(): void {
    if (this.busy() || this.newName.trim().length < 3) return;
    this.busy.set(true); this.error.set('');
    this.api.createOrganization(this.newName.trim()).subscribe({next:org=>{
      this.busy.set(false);this.newName='';this.selectedId.set(org.id);
      this.info.set('Organización creada.');this.refresh();
    },error:err=>this.failure(err)});
  }
  invite(): void {
    const id=this.selectedId();if(!id||!this.canManage||!this.inviteEmail.trim())return;
    this.busy.set(true);this.error.set('');this.invitationCode.set('');
    this.api.inviteMember(id,this.inviteEmail.trim(),this.inviteRole).subscribe({next:response=>{
      this.busy.set(false);this.inviteEmail='';this.invitationCode.set(response.token);
      this.info.set('Invitación creada. Copia el código y compártelo de forma privada.');this.load();
    },error:err=>this.failure(err)});
  }
  accept(): void {
    if (this.busy()||!this.acceptCode.trim())return;
    this.busy.set(true);this.error.set('');
    this.api.acceptInvitation(this.acceptCode.trim()).subscribe({next:org=>{
      this.busy.set(false);this.acceptCode='';this.selectedId.set(org.id);
      this.info.set('Ya formas parte de la organización.');
      this.refresh();this.vm.loadProjects();
    },error:err=>this.failure(err)});
  }
  changeRole(user:OrganizationMember,role:'viewer'|'developer'|'admin'):void {
    const id=this.selectedId();if(!id||!this.canManage||user.role==='owner')return;
    this.api.updateMemberRole(id,user.id,role).subscribe({next:()=>this.load(),error:err=>this.failure(err)});
  }
  remove(user:OrganizationMember):void {
    const id=this.selectedId();if(!id||!this.canManage||user.role==='owner'||this.busy())return;
    if(!confirm('¿Quitar a '+user.display_name+' de esta organización?'))return;
    this.busy.set(true);
    this.api.removeMember(id,user.id).subscribe({next:()=>{this.busy.set(false);this.load();},error:err=>this.failure(err)});
  }
  downloadReport():void {
    const id=this.selectedId();if(!id||this.busy())return;
    this.busy.set(true);
    this.api.organizationReportCsv(id).subscribe({next:blob=>{
      this.busy.set(false);
      const url=URL.createObjectURL(blob);const link=document.createElement('a');
      link.href=url;link.download='legacylift-reporte-proyectos.csv';link.click();
      URL.revokeObjectURL(url);
    },error:err=>this.failure(err)});
  }
}
