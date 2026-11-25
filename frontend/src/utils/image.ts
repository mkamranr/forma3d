/** Reference photos are downscaled in the browser before upload: a vision model needs ~1–1.5k px at most,
 * and smaller images upload faster and cost fewer vision tokens. */
export const MAX_IMAGE_EDGE = 1536
export const MAX_IMAGE_BYTES = 10 * 1024 * 1024
export const IMAGE_TYPES = ['image/png', 'image/jpeg']

export class ImageRejected extends Error {}

export function checkImageFile(file: File): void {
  if (!IMAGE_TYPES.includes(file.type)) throw new ImageRejected(`${file.name}: only PNG and JPEG images are supported`)
  if (file.size > 4 * MAX_IMAGE_BYTES) throw new ImageRejected(`${file.name} is too large`)
}

/** Returns a JPEG no larger than MAX_IMAGE_EDGE (transparent areas become white), or the file itself
 * when it is already small enough or the browser cannot decode it here (tests, old browsers). */
export async function prepareImage(file: File): Promise<File> {
  checkImageFile(file)
  if (typeof createImageBitmap !== 'function' || typeof document === 'undefined') return file
  let bitmap: ImageBitmap
  try {
    bitmap = await createImageBitmap(file)
  } catch {
    throw new ImageRejected(`${file.name} could not be read as an image`)
  }
  const scale = Math.min(1, MAX_IMAGE_EDGE / Math.max(bitmap.width, bitmap.height))
  if (scale === 1 && file.size <= 2 * 1024 * 1024) {
    bitmap.close?.()
    return file
  }
  const canvas = document.createElement('canvas')
  canvas.width = Math.max(1, Math.round(bitmap.width * scale))
  canvas.height = Math.max(1, Math.round(bitmap.height * scale))
  const ctx = canvas.getContext('2d')
  if (!ctx) return file
  ctx.fillStyle = '#ffffff'
  ctx.fillRect(0, 0, canvas.width, canvas.height)
  ctx.drawImage(bitmap, 0, 0, canvas.width, canvas.height)
  bitmap.close?.()
  const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.9))
  if (!blob) return file
  const name = file.name.replace(/\.[^.]+$/, '') + '.jpg'
  return new File([blob], name, { type: 'image/jpeg' })
}
