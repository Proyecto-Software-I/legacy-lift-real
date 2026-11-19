import { Component, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { WorkspaceFacade } from '../../../core/state/workspace.facade';
import { Provider } from '../../../core/api/api.service';
import { IconComponent, ButtonDirective, DialogComponent } from '../../../shared/ui';

@Component({
  selector: 'll-project-dialogs',
  standalone: true,
  imports: [CommonModule, FormsModule, IconComponent, ButtonDirective, DialogComponent],
  templateUrl: './project-dialogs.component.html',
  styleUrl: './project-dialogs.component.css'
})
export class ProjectDialogsComponent {
  readonly vm = inject(WorkspaceFacade);
  engineHint(engine: Provider): string {
    if (engine.id === 'demo') return 'Determinista y local. No envía código a ningún servicio.';
    if (!engine.ready) return engine.id === 'gemini' ? 'Instala Antigravity CLI (agy) e inicia sesión con tu cuenta de Google en el servidor.'
      : 'No configurado en este servidor.';
    const account = ({codex:'tu sesión de ChatGPT (Codex CLI)', gemini:'tu cuenta de Google AI Pro (Antigravity CLI)', claude:'la API key de Anthropic del servidor'} as Record<string,string>)[engine.id];
    return `Usa ${account}. Envía fragmentos de código solo cuando lo autorices.`;
  }
}
