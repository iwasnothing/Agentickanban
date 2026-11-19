// Redirect unauthenticated visitors on the board route to /login.
// Cookie presence only; API routes still verify the signed session.
export const config = { matcher: ["/", "/index.html"] };

export default function middleware(request: Request): Response | undefined {
  const cookie = request.headers.get("cookie") || "";
  if (!/(?:^|;\s*)ak_session=/.test(cookie)) {
    return Response.redirect(new URL("/login", request.url), 303);
  }
  return undefined;
}
