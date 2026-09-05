main:
  (start, start) -> (start, start, main,      S, R)
  (start, _) ->     (start, 1,     scan_left, R, S)
  (a, b) ->         (a,     b,     scan_left, R, S)
scan_left:
  (_, b) ->     (_, b,     acc,        S, S)
  (a, 1) ->     (a, 0,     copy,       S, R)
  (a, 0) ->     (a, 0,     scan_left,  S, L)
  (a, start) -> (a, start, erase,      S, R)
erase:
  (a, _) -> (a, _, main,  S, L)
  (a, b) -> (a, 1, erase, S, R)
copy:
  (a, _) -> (a, 0, scan_left, S, L)
  (a, 0) -> (a, 0, copy,      S, R)
