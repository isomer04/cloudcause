/**
 * Client-side coordination between the rail and the console.
 *
 * The console holds a finished report in React state, and the rail is a sibling
 * subtree rendered by the root layout. Clicking "Investigate" while already on
 * `/` is a same-route soft navigation, so the console never remounts and the
 * report stays on screen - the link looks broken because nothing happens.
 *
 * A window event is the smallest fix that keeps the boundaries this repo wants.
 * The alternative, a React context, means wrapping the layout in a client
 * provider so a server-rendered rail can talk to a client console, which is a
 * larger client boundary for strictly less.
 */
export const NEW_INVESTIGATION_EVENT = "cloudcause:new-investigation";
