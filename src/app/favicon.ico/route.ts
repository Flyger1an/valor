const icon = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
  <rect width="64" height="64" rx="12" fill="#090b0f"/>
  <path d="M14 42 26 18l8 17 6-10 10 17" fill="none" stroke="#4db9ff" stroke-width="6" stroke-linecap="round" stroke-linejoin="round"/>
  <circle cx="50" cy="42" r="4" fill="#37d68b"/>
</svg>`;

export function GET() {
  return new Response(icon, {
    headers: {
      "cache-control": "public, max-age=31536000, immutable",
      "content-type": "image/svg+xml",
    },
  });
}
