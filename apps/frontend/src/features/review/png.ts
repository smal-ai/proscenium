/** Encode canonical PNG colour type 0, 8-bit, no alpha/profile. Browser canvas PNG is RGBA. */
function crc32(bytes: Uint8Array): number {
  let crc = 0xffffffff;
  for (const byte of bytes) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit++) crc = (crc >>> 1) ^ (0xedb88320 & -(crc & 1));
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function chunk(name: string, bytes: Uint8Array): Uint8Array<ArrayBuffer> {
  const result = new Uint8Array(bytes.length + 12);
  const view = new DataView(result.buffer);
  view.setUint32(0, bytes.length);
  result.set(new TextEncoder().encode(name), 4);
  result.set(bytes, 8);
  view.setUint32(bytes.length + 8, crc32(result.subarray(4, bytes.length + 8)));
  return result;
}

export async function greyPng(width: number, height: number, pixels: Uint8Array): Promise<Blob> {
  if (pixels.length !== width * height) throw new Error("Invalid mask dimensions");
  const raw = new Uint8Array((width + 1) * height);
  for (let y = 0; y < height; y++) raw.set(pixels.subarray(y * width, (y + 1) * width), y * (width + 1) + 1);
  const compressed = new Uint8Array(await new Response(new Blob([raw]).stream().pipeThrough(new CompressionStream("deflate"))).arrayBuffer());
  const header = new Uint8Array(13);
  const view = new DataView(header.buffer);
  view.setUint32(0, width); view.setUint32(4, height); header[8] = 8;
  return new Blob([new Uint8Array([137,80,78,71,13,10,26,10]), chunk("IHDR", header), chunk("IDAT", compressed), chunk("IEND", new Uint8Array())], {type: "image/png"});
}
