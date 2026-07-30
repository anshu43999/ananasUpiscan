export async function copyText(text: string): Promise<void> {
  const value = String(text || '');
  const clipboard = globalThis.navigator?.clipboard;
  if (clipboard && typeof clipboard.writeText === 'function') {
    try {
      await clipboard.writeText(value);
      return;
    } catch {
      // Fall through to the textarea fallback for non-HTTPS or denied clipboard access.
    }
  }

  const doc = globalThis.document;
  if (!doc?.body) {
    throw new Error('当前浏览器不支持复制到剪贴板');
  }

  const textarea = doc.createElement('textarea');
  textarea.value = value;
  textarea.setAttribute('readonly', 'true');
  textarea.style.position = 'fixed';
  textarea.style.left = '-9999px';
  textarea.style.top = '0';
  doc.body.appendChild(textarea);

  const selection = doc.getSelection();
  const previousRange = selection && selection.rangeCount > 0 ? selection.getRangeAt(0) : null;
  textarea.focus();
  textarea.select();

  try {
    const copied = doc.execCommand('copy');
    if (!copied) {
      throw new Error('复制失败，请手动复制导出内容');
    }
  } finally {
    doc.body.removeChild(textarea);
    if (previousRange && selection) {
      selection.removeAllRanges();
      selection.addRange(previousRange);
    }
  }
}
