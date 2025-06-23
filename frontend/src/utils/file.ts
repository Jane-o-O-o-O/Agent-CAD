
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