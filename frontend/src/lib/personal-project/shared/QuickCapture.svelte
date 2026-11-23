<script lang="ts">
 import { getContext, onMount } from 'svelte';
 import type { MemoBridge } from './memo-context';
 import { localDay } from './capture';
 import DailyPlan from './DailyPlan.svelte';
 const bridge = getContext<MemoBridge | undefined>('netaq-memo');
 let dialog: HTMLDialogElement;
 let visible = $state(false), date = $state(localDay());
 function open(day = localDay()) {
  if (visible) return;
  date = day;
  visible = true;
  dialog.showModal();
 }
 function close() { visible = false; dialog.close(); }
 $effect(() => { if (bridge) { bridge.render = memoForm; bridge.modalOpen = visible; } });
 $effect(() => { if (!visible && bridge?.day) date = bridge.day; });
 onMount(() => {
  const handler = (e: Event) => open((e as CustomEvent<{ date?: string }>).detail?.date || localDay());
  const shortcut = (e: KeyboardEvent) => { if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key.toLowerCase() === 'm') { e.preventDefault(); open(); } };
  window.addEventListener('netaq-open-memo', handler);
  window.addEventListener('keydown', shortcut);
  if (location.hash === '#daily-note') open();
  return () => { window.removeEventListener('netaq-open-memo', handler); window.removeEventListener('keydown', shortcut); };
 });
</script>
{#snippet memoForm(inline: boolean)}
 <div class:embedded={inline} class="memo-content">
  {#if !inline}<header><h2 id="memo-title">오늘의 메모</h2><button type="button" aria-label="메모 닫기" onclick={close}>×</button></header>{/if}
  {#if !inline}<a class="inbox-link" href="/personal-project/calendar/inbox">이전 빠른 기록 보기</a>{/if}
  {#key date}<DailyPlan {date} tasks={[]} projects={[]} notesOnly ondirty={() => {}} onedit={() => {}} oncomplete={() => {}} />{/key}
 </div>
{/snippet}
<button class="capture-trigger" onclick={() => open()}>빠른 기록</button>
<dialog class="memo-dialog" bind:this={dialog} aria-labelledby="memo-title" oncancel={(e) => { e.preventDefault(); close(); }} onclick={(e) => { if (e.target === dialog) close(); }}>
 {#if visible}{@render memoForm(false)}{/if}
</dialog>
<style>
 .capture-trigger {border:0;background:var(--ondo-accent,#c4412c);color:white;border-radius:8px;padding:12px 20px;font:inherit;font-size:15px;font-weight:700;cursor:pointer;min-height:44px;min-width:144px}
 .memo-dialog {position:fixed;inset:0;margin:auto;box-sizing:border-box;width:min(680px,calc(100vw - 32px));max-height:calc(100dvh - 32px);padding:24px;border:1px solid var(--ondo-line,#e2e3e7);border-radius:16px;color:var(--ondo-ink,#242529);background:white;overflow:auto}
 .memo-dialog::backdrop {background:#14151959}
 .memo-content header {display:flex;align-items:center;justify-content:space-between;gap:12px}
 .memo-content h2 {font-size:22px;margin:0}
 .inbox-link {display:inline-block;margin:8px 0;color:var(--ondo-muted,#696b73);font-size:13px}
 .memo-content header button {background:none;border:0;font:inherit;font-size:26px;min-width:44px;min-height:44px;cursor:pointer}
 .memo-content :global(.daily-plan) {border:0;padding:0;background:transparent}
 .embedded {height:100%}
 @media(max-width:760px){.capture-trigger{min-width:116px;font-size:14px;padding:10px 16px}.memo-dialog{padding:20px}}
</style>
