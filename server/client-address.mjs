import {isIP} from 'node:net';

// Caddy replaces X-Forwarded-For. Trust it only from our explicitly enabled,
// local proxy; direct deployments must ignore client-supplied forwarding headers.
export function clientAddress(req, trustProxy=process.env.TRUST_PROXY) {
  const peer=req.socket.remoteAddress||'unknown';
  if(trustProxy!=='loopback'||!['127.0.0.1','::1','::ffff:127.0.0.1'].includes(peer))return peer;
  const forwarded=req.headers['x-forwarded-for'];
  if(typeof forwarded!=='string')return peer;
  const address=forwarded.split(',').at(-1).trim();
  return isIP(address)?address:peer;
}
