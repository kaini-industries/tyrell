#!/usr/bin/env python3
"""Optional pinned python-chess differential rules check; no runtime dependency."""
import argparse
import chess
import subprocess
import random
import json

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--adapter',required=True,help='Path to the separately compiled adapter')
args=parser.parse_args()
if chess.__version__ != '1.11.2':
    raise SystemExit('This deterministic check requires the chess==1.11.2 PyPI distribution')
rng=random.Random(20261115)
p=subprocess.Popen([args.adapter],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True,bufsize=1)
checked=0

def check(b,pos=518):
 global checked
 f=b.shredder_fen(en_passant='fen') if b.chess960 else b.fen(en_passant='fen')
 p.stdin.write(f'position {int(b.chess960)} {pos} {f}\n');p.stdin.flush()
 answer=p.stdout.readline().strip();moves,material=answer.split('|')
 actual=set(moves.split()); expected=set()
 for m in b.legal_moves:
  if b.is_castling(m):
   expected.add(chess.square_name(m.from_square)+('g' if b.is_kingside_castling(m) else 'c')+str(chess.square_rank(m.from_square)+1)+'c')
  else:expected.add(m.uci())
 flags=[int(b.has_insufficient_material(c)) for c in (chess.WHITE,chess.BLACK)]
 if actual!=expected or [int(i) for i in material.split()]!=flags:
  print(json.dumps({'index':checked,'position':pos,'fen':f,'extra':sorted(actual-expected),'missing':sorted(expected-actual),'actual_material':material,'expected_material':flags}));return False
 checked+=1;return True

for n in range(200):
 b=chess.Board()
 for ply in range(150):
  if not check(b):raise SystemExit(1)
  moves=list(b.legal_moves)
  if not moves:break
  # Prefer promotions/captures/castles for more special move and endgame coverage.
  weights=[5 if b.is_capture(m) or b.is_castling(m) or m.promotion else 1 for m in moves]
  b.push(rng.choices(moves,weights=weights)[0])
for pos in range(960):
 b=chess.Board.from_chess960_pos(pos)
 for ply in range(36):
  if not check(b,pos):raise SystemExit(1)
  moves=list(b.legal_moves)
  if not moves:break
  b.push(rng.choice(moves))
 # Strip middle pieces to exercise all immediate castle geometry. Leave only
 # white king+rooks and the black king; black has no castle rights.
 b=chess.Board.from_chess960_pos(pos)
 for q,piece in list(b.piece_map().items()):
  if not(piece.color==chess.WHITE and piece.piece_type in(chess.KING,chess.ROOK)) and q!=b.king(chess.BLACK):b.remove_piece_at(q)
 b.castling_rights &= chess.BB_RANK_1
 if b.is_valid() and not check(b,pos):raise SystemExit(1)
 # Test enemy line attacks with one black rook/bishop/queen on empty squares.
 for kind in(chess.ROOK,chess.BISHOP,chess.QUEEN):
  for q in rng.sample([q for q in chess.SQUARES if b.piece_at(q) is None],8):
   b.set_piece_at(q,chess.Piece(kind,chess.BLACK))
   if b.is_valid() and not check(b,pos):raise SystemExit(1)
   b.remove_piece_at(q)
rules_checked=checked

rng=random.Random(66)
checked=0
for i in range(200):
 pos=-1 if i<100 else rng.randrange(960)
 b=chess.Board() if pos<0 else chess.Board.from_chess960_pos(pos)
 tokens=[]
 for ply in range(70):
  if b.is_game_over(claim_draw=True):break
  moves=list(b.legal_moves);m=rng.choice(moves);san=b.san(m)
  token=chess.square_name(m.from_square)+('g' if b.is_kingside_castling(m) else 'c')+str(chess.square_rank(m.from_square)+1)+'c' if b.is_castling(m) else m.uci()
  tokens.append(token);b.push(m)
  p.stdin.write('sequence '+str(pos)+' '+' '.join(tokens)+'\n');p.stdin.flush();answer=p.stdout.readline().strip()
  if answer=='error':raise RuntimeError((i,ply,tokens))
  actual,fen=answer.split('|')
  expected_fen=b.shredder_fen(en_passant='fen') if b.chess960 else b.fen(en_passant='fen')
  if actual!=san or fen!=expected_fen:
   print(json.dumps({'game':i,'ply':ply,'position':pos,'moves':tokens,'san':san,'actual':actual,'expected_fen':expected_fen,'actual_fen':fen}));raise SystemExit(1)
  checked+=1
print(json.dumps({'rules_and_material_positions':rules_checked,'san_and_replayed_fen_positions':checked,'chess_distribution_version':chess.__version__,'passed':True}))
p.stdin.close()
if p.wait() != 0: raise SystemExit('Oracle adapter exited unsuccessfully')
