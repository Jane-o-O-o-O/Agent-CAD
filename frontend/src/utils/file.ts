/**
 * Format file size from bytes to human readable format
 * @param bytes - File size in bytes
 * @param decimals - Number of decimal places (default: 1)
 * @returns Formatted file size string
 */
export function formatFileSize(bytes: number, decimals: number = 1): string {
  if (bytes === 0) return '0 B';

  const k = 1024;
  const dm = decimals < 0 ? 0 : decimals;
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB', 'PB', 'EB', 'ZB', 'YB'];

  const i = Math.floor(Math.log(bytes) / Math.log(k));

  return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + ' ' + sizes[i];
}

/**
 * Convert KB to bytes
 * @param kb - Size in KB
 * @returns Size in bytes
 */
export function kbToBytes(kb: number): number {
  return kb * 1024;
}

/**
 * Convert bytes to KB
 * @param bytes - Size in bytes
 * @returns Size in KB
 */
export function bytesToKb(bytes: number): number {
  return bytes / 1024;
} 