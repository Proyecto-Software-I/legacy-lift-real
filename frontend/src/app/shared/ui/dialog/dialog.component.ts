import { ChangeDetectionStrategy, Component, ElementRef, EventEmitter, Input, Output, ViewChild } from '@angular/core';
import { CommonModule } from '@angular/common';
import { IconComponent } from '../icon/icon.component';

/** Native modal: browser-managed focus trapping, Escape and focus restoration. */
@Component({
  selector: 'll-dialog',
  standalone: true,
  imports: [CommonModule, IconComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './dialog.component.html',
  styleUrl: './dialog.component.css'
})
export class DialogComponent {
  @Input() open = false;
  @Input({required:true}) title = '';
  @Input({required:true}) dialogId = '';
  /** Kept for template compatibility; dialogs no longer render a kicker. */
  @Input() eyebrow = '';
  @Output() closed = new EventEmitter<void>();

  @ViewChild('nativeDialog') set nativeDialog(ref: ElementRef<HTMLDialogElement> | undefined) {
    if (!ref) return;
    queueMicrotask(() => {
      const dialog = ref.nativeElement;
      if (dialog.isConnected && !dialog.open) dialog.showModal();
    });
  }
  cancel(event: Event): void { event.preventDefault(); this.closed.emit(); }
  onDialogClick(event: MouseEvent): void {
    // Only consider clicks whose target is the <dialog> element itself (backdrop area).
    // Native <select>/<option> popups fire clicks with target inside the dialog and clientX/Y=0 or outside rect.
    if (event.target !== event.currentTarget) return;
    if (event.clientX === 0 && event.clientY === 0) return;
    const rect = (event.currentTarget as HTMLDialogElement).getBoundingClientRect();
    if (event.clientX < rect.left || event.clientX > rect.right ||
        event.clientY < rect.top || event.clientY > rect.bottom) this.closed.emit();
  }
}
