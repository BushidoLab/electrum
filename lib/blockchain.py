# Electrum - lightweight Bitcoin client
# Copyright (C) 2012 thomasv@ecdsa.org
#
# Permission is hereby granted, free of charge, to any person
# obtaining a copy of this software and associated documentation files
# (the "Software"), to deal in the Software without restriction,
# including without limitation the rights to use, copy, modify, merge,
# publish, distribute, sublicense, and/or sell copies of the Software,
# and to permit persons to whom the Software is furnished to do so,
# subject to the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
# NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS
# BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN
# ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
# CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
import os
import threading
import struct
from io import BytesIO

from . import util
from . import bitcoin
from .bitcoin import *
import base64

from .equihash import is_gbp_valid
import logging
logging.basicConfig(level=logging.INFO)

MAX_TARGET = 0x00000000FFFF0000000000000000000000000000000000000000000000000000

def serialize_header(res):
    r = b''
    r += struct.pack("<i", res.get('version'))
    r += str_to_hash(res.get('prev_block_hash'))
    r += str_to_hash(res.get('merkle_root'))
    r += str_to_hash(res.get('hash_reserved'))
    r += struct.pack("<I", res.get('timestamp'))
    r += struct.pack("<I", res.get('bits'))
    r += str_to_hash(res.get('nonce'))
    r += ser_char_vector(base64.b64decode(res.get('n_solution').encode('utf8')))
    return r

def deserialize_header(f, height):
    h = {}
    h['version'] = struct.unpack("<I", f.read(4))[0]
    h['prev_block_hash'] = hash_to_str(f.read(32))
    h['merkle_root'] = hash_to_str(f.read(32))
    h['hash_reserved'] = hash_to_str(f.read(32))
    h['timestamp'] = struct.unpack("<I", f.read(4))[0]
    h['bits'] = struct.unpack("<I", f.read(4))[0]
    h['nonce'] = hash_to_str(f.read(32))
    h['n_solution'] = base64.b64encode(bytes(deser_char_vector(f))).decode('utf8')
    h['block_height'] = height
    return h

def sha256_header(header):
    return uint256_from_bytes(Hash(serialize_header(header)))

def hash_header(header):
    if header is None:
        return '0' * 64
    if header.get('prev_block_hash') is None:
        header['prev_block_hash'] = '00'*64
    return hash_to_str(Hash(serialize_header(header)))


blockchains = {}

def read_blockchains(config):
    blockchains[0] = Blockchain(config, 0, None)
    fdir = os.path.join(util.get_headers_dir(config), 'forks')
    if not os.path.exists(fdir):
        os.mkdir(fdir)
    l = filter(lambda x: x.startswith('fork_'), os.listdir(fdir))
    l = sorted(l, key = lambda x: int(x.split('_')[1]))
    for filename in l:
        checkpoint = int(filename.split('_')[2])
        parent_id = int(filename.split('_')[1])
        b = Blockchain(config, checkpoint, parent_id)
        h = b.read_header(b.checkpoint)
        if b.parent().can_connect(h, check_height=False):
            blockchains[b.checkpoint] = b
        else:
            util.print_error("cannot connect", filename)
    return blockchains

def check_header(header):
    if type(header) is not dict:
        return False
    for b in blockchains.values():
        if b.check_header(header):
            return b
    return False

def can_connect(header):
    for b in blockchains.values():
        if b.can_connect(header):
            return b
    return False


class Blockchain(util.PrintError):
    """
    Manages blockchain headers and their verification
    """

    def __init__(self, config, checkpoint, parent_id):
        self.config = config
        self.catch_up = None # interface catching up
        self.checkpoint = checkpoint
        self.checkpoints = bitcoin.NetworkConstants.CHECKPOINTS
        self.parent_id = parent_id
        self.lock = threading.Lock()
        with self.lock:
            self.update_size()

    def parent(self):
        return blockchains[self.parent_id]

    def get_max_child(self):
        children = list(filter(lambda y: y.parent_id==self.checkpoint, blockchains.values()))
        return max([x.checkpoint for x in children]) if children else None

    def get_checkpoint(self):
        mc = self.get_max_child()
        return mc if mc is not None else self.checkpoint

    def get_branch_size(self):
        return self.height() - self.get_checkpoint() + 1

    def get_name(self):
        return self.get_hash(self.get_checkpoint()).lstrip('00')[0:10]

    def check_header(self, header):
        header_hash = hash_header(header)
        height = header.get('block_height')
        return header_hash == self.get_hash(height)

    def fork(parent, header):
        checkpoint = header.get('block_height')
        self = Blockchain(parent.config, checkpoint, parent.checkpoint)
        open(self.path(), 'w+').close()
        self.save_header(header)
        return self

    def height(self):
        return self.checkpoint + self.size() - 1

    def size(self):
        with self.lock:
            return self._size

    def update_size(self):
        p = self.path()
        self._size = 0
        if os.path.exists(p):
            with open(p, 'rb') as f:
                f.seek(0, 2)
                eof = f.tell()
                f.seek(0, 0)
                while True:
                    try:
                        f.seek(bitcoin.BASIC_HEADER_SIZE, 1)
                        vs = read_vector_size(f)
                        f.seek(vs, 1)
                        if f.tell() < eof:
                            self._size += 1
                    except:
                        break

    def verify_header(self, header, prev_header, target):
        prev_hash = hash_header(prev_header)
        _powhash = sha256_header(header)
        if prev_hash != header.get('prev_block_hash'):
            raise BaseException("prev hash mismatch: %s vs %s" % (prev_hash, header.get('prev_block_hash')))
        target = self.bits_to_target(header['bits'])
        if _powhash > target:
            raise BaseException("insufficient proof of work: %s vs target %s" % (int('0x' + _powhash, 16), target))
        nonce = uint256_from_bytes(str_to_hash(header.get('nonce')))
        n_solution = vector_from_bytes(base64.b64decode(header.get('n_solution').encode('utf8')))
        if not is_gbp_valid(serialize_header(header), nonce, n_solution,
            bitcoin.NetworkConstants.EQUIHASH_N, bitcoin.NetworkConstants.EQUIHASH_K):
            raise BaseException("Equihash invalid")

    def verify_chunk(self, index, data):
        num = len(data) / 1484
        prev_header = None
        if index != 0:
            prev_header = self.read_header(index * 2016 - 1)
        headers = {}
        for i in range(num):
            raw_header = data[i * 1484:(i + 1) * 1484]
            header = self.deserialize_header(raw_header, index * 2016 + i)
            headers[header.get('block_height')] = header
            nonce, n_solution = headers.get('nonce'), header.get('n_solution')
            bits, target = self.get_target(index * 2016 + i, headers)
            self.verify_header(header, prev_header, bits, target, nonce, n_solution)
            prev_header = header

    def path(self):
        d = util.get_headers_dir(self.config)
        filename = 'blockchain_headers' if self.parent_id is None else os.path.join('forks', 'fork_%d_%d'%(self.parent_id, self.checkpoint))
        return os.path.join(d, filename)

    def save_chunk(self, index, chunk):
        filename = self.path()
        with open(filename, 'rb') as f:
            d = self._height_to_offset(f, index * 2016 - self.checkpoint)
        if d < 0:
            chunk = chunk[-d:]
            d = 0
        self.write(chunk, d, index > len(self.checkpoints))
        self.swap_with_parent()

    def swap_with_parent(self):
        if self.parent_id is None:
            return
        parent_branch_size = self.parent().height() - self.checkpoint + 1
        if parent_branch_size >= self.size():
            return
        self.print_error("swap", self.checkpoint, self.parent_id)
        parent_id = self.parent_id
        checkpoint = self.checkpoint
        parent = self.parent()
        with open(self.path(), 'rb') as f:
            my_data = f.read()
        with open(parent.path(), 'rb') as f:
            self._height_to_offset
            offset = self._height_to_offset(f, checkpoint - parent.checkpoint)
            length = self._height_to_offset(f, parent_branch_size, offset)
            f.seek(offset)
            parent_data = f.read(length)

        self.write(parent_data, 0)
        parent.write(my_data, checkpoint - parent.checkpoint)
        # store file path
        for b in blockchains.values():
            b.old_path = b.path()
        # swap parameters
        self.parent_id = parent.parent_id; parent.parent_id = parent_id
        self.checkpoint = parent.checkpoint; parent.checkpoint = checkpoint
        self._size = parent._size; parent._size = parent_branch_size
        # move files
        for b in blockchains.values():
            if b in [self, parent]: continue
            if b.old_path != b.path():
                self.print_error("renaming", b.old_path, b.path())
                os.rename(b.old_path, b.path())
        # update pointers
        blockchains[self.checkpoint] = self
        blockchains[parent.checkpoint] = parent

    def _height_to_offset(self, f, height, start=0):
        pos = f.tell()
        f.seek(0, 2)
        eof = f.tell()
        f.seek(start, 0)
        for i in range(height):
            f.seek(bitcoin.BASIC_HEADER_SIZE, 1)
            vs = read_vector_size(f)
            f.seek(vs, 1)
            if f.tell() >= eof:
                raise Exception('Out of file')
        f.seek(pos, 0)
        return f.tell()

    def write(self, data, delta, truncate=False):
        filename = self.path()
        with self.lock:
            with open(filename, 'rb+') as f:
                f.seek(0, 2)
                eof = f.tell()
                offset = self._height_to_offset(f, delta)
                f.seek(offset)
                if truncate and offset < eof:
                    f.truncate()
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            self.update_size()

    def save_header(self, header):
        delta = header.get('block_height') - self.checkpoint
        data = serialize_header(header)
        assert delta == self.size()
        self.write(data, delta)
        self.swap_with_parent()

    def read_header(self, height):
        assert self.parent_id != self.checkpoint
        if height < 0:
            return
        if height < self.checkpoint:
            return self.parent().read_header(height)
        if height > self.height():
            return

        delta = height - self.checkpoint
        name = self.path()
        if os.path.exists(name):
            with open(name, 'rb') as f:
                for i in range(delta):
                    f.seek(bitcoin.BASIC_HEADER_SIZE, 1)
                    vs = read_vector_size(f)
                    f.seek(vs, 1)
                h = deserialize_header(f, height)
        return h

    def get_hash(self, height):
        return self.hash_header(self.read_header(height))

    def hash_header(self, header):
        return hash_header(header)

    def get_target(self, index):
        # compute target from chunk x, used in chunk x+1
        if bitcoin.NetworkConstants.TESTNET:
            return 0
        if index == -1:
            return MAX_TARGET
        if index < len(self.checkpoints):
            h, t = self.checkpoints[index]
            return t
        # new target
        first = self.read_header(index * 2016)
        last = self.read_header(index * 2016 + 2015)
        bits = last.get('bits')
        target = self.bits_to_target(bits)
        nActualTimespan = last.get('timestamp') - first.get('timestamp')
        nTargetTimespan = 14 * 24 * 60 * 60
        nActualTimespan = max(nActualTimespan, nTargetTimespan // 4)
        nActualTimespan = min(nActualTimespan, nTargetTimespan * 4)
        new_target = min(MAX_TARGET, (target * nActualTimespan) // nTargetTimespan)
        return new_target

    def bits_to_target(self, bits):
        bitsN = (bits >> 24) & 0xff
        if not (bitsN >= 0x03 and bitsN <= 0x1d):
            raise BaseException("First part of bits should be in [0x03, 0x1d]")
        bitsBase = bits & 0xffffff
        if not (bitsBase >= 0x8000 and bitsBase <= 0x7fffff):
            raise BaseException("Second part of bits should be in [0x8000, 0x7fffff]")
        return bitsBase << (8 * (bitsN-3))

    def target_to_bits(self, target):
        c = ("%064x" % target)[2:]
        while c[:2] == '00' and len(c) > 6:
            c = c[2:]
        bitsN, bitsBase = len(c) // 2, int('0x' + c[:6], 16)
        if bitsBase >= 0x800000:
            bitsN += 1
            bitsBase >>= 8
        return bitsN << 24 | bitsBase

    def can_connect(self, header, check_height=True):
        height = header['block_height']
        if check_height and self.height() != height - 1:
            self.print_error("cannot connect at height", height)
            return False
        if height == 0:
            return hash_header(header) == bitcoin.NetworkConstants.GENESIS
        try:
            prev_header = self.read_header(height - 1)
            prev_hash = self.hash_header(prev_header)
        except:
            return False
        if prev_hash != header.get('prev_block_hash'):
            return False
        target = self.get_target(height // 2016 - 1)
        try:
            self.verify_header(header, prev_header, target)
        except BaseException as e:
            import traceback
            traceback.print_exc()
            self.print_error('verify_header failed', str(e))
            return False
        return True

    def connect_chunk(self, idx, hexdata):
        try:
            data = bfh(hexdata)
            self.verify_chunk(idx, data)
            #self.print_error("validated chunk %d" % idx)
            self.save_chunk(idx, data)
            return True
        except BaseException as e:
            self.print_error('verify_chunk failed', str(e))
            return False

    def get_checkpoints(self):
        # for each chunk, store the hash of the last block and the target after the chunk
        cp = []
        n = self.height() // 2016
        for index in range(n):
            h = self.get_hash((index+1) * 2016 -1)
            target = self.get_target(index)
            cp.append((h, target))
        return cp
