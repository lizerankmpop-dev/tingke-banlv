# -*- coding: utf-8 -*-
"""合并 _vocab_batch_*.json 到 basic_vocab.json（临时脚本，用完删除）"""
import json
import glob
import os

os.chdir(os.path.dirname(os.path.abspath(__file__)))

with open("basic_vocab.json", encoding="utf-8") as f:
    vocab = json.load(f)

print("现有词数:", len(vocab))
added = skipped = 0
for path in sorted(glob.glob("_vocab_batch_*.json")):
    with open(path, encoding="utf-8") as f:
        batch = json.load(f)
    ok = dup = 0
    for w, info in batch.items():
        if w in vocab:
            dup += 1
        else:
            vocab[w] = info
            ok += 1
    print(f"{os.path.basename(path)}: {len(batch)} 词 (新增 {ok}, 重复 {dup})")
    added += ok
    skipped += dup

with open("basic_vocab.json", "w", encoding="utf-8") as f:
    json.dump(vocab, f, ensure_ascii=False, indent=2)

print(f"合并后总词数: {len(vocab)}")
print(f"新增: {added}, 跳过重复: {skipped}")
