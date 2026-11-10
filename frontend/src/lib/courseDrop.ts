export function readCourseURL(data: Pick<DataTransfer, 'getData'>): string | null {
  const uriList = data.getData('text/uri-list')
  const text = uriList
    ? uriList.split(/\r?\n/).map(line => line.trim()).find(line => line && !line.startsWith('#'))
    : data.getData('text/plain').trim()

  if (!text || !/^https:\/\/[^\s]+$/i.test(text)) return null
  try {
    const url = new URL(text)
    const host = url.hostname.replace(/\.$/, '')
    if (url.username || url.password || !host.includes('.') ||
        /^[0-9.]+$/.test(host) || /(?:\.(localhost|local|internal)|(?:^|\.)home\.arpa)$/.test(host)) return null
    return url.href
  } catch {
    return null
  }
}
