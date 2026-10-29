/* eslint-disable @next/next/no-img-element */

// The company logo. Logo_siri.jpg has a white background, which shows as a
// white box in dark mode — there Logo_siri_t.png is used instead: the same
// image with only the OUTER white made transparent (flood-filled from the
// edges, so the white stripes in the globe and the name banner stay).
//   className — size/layout, both versions
//   tile      — the light version's frame (background, padding, rounding…)
export function Logo({ className = "", tile = "", alt = "" }: { className?: string; tile?: string; alt?: string }) {
  return (
    <>
      <img src="/Logo_siri.jpg" alt={alt} className={`${className} ${tile} object-cover dark:hidden`} />
      <img src="/Logo_siri_t.png" alt={alt} className={`${className} hidden object-contain dark:block`} />
    </>
  );
}
