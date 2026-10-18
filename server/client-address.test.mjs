import {test} from 'node:test';
import assert from 'node:assert/strict';
import {clientAddress} from './client-address.mjs';

const request=(peer,forwarded)=>({socket:{remoteAddress:peer},headers:{'x-forwarded-for':forwarded}});

test('direct clients cannot spoof their rate-limit address',()=>{
  assert.equal(clientAddress(request('198.51.100.10','203.0.113.1'),'loopback'),'198.51.100.10');
  assert.equal(clientAddress(request('127.0.0.1','203.0.113.1'),''),'127.0.0.1');
});

test('enabled local proxy preserves separate IPv4 and IPv6 client identities',()=>{
  assert.equal(clientAddress(request('127.0.0.1','198.51.100.10'),'loopback'),'198.51.100.10');
  assert.equal(clientAddress(request('127.0.0.1','198.51.100.11'),'loopback'),'198.51.100.11');
  assert.equal(clientAddress(request('::1','2001:db8::1'),'loopback'),'2001:db8::1');
  assert.equal(clientAddress(request('::ffff:127.0.0.1','203.0.113.99, 198.51.100.10'),'loopback'),'198.51.100.10');
});

test('missing or malformed proxy addresses fall back to the socket peer',()=>{
  for(const value of [undefined,'','not-an-ip','198.51.100.1:1234',['198.51.100.1']]){
    assert.equal(clientAddress(request('127.0.0.1',value),'loopback'),'127.0.0.1');
  }
});
