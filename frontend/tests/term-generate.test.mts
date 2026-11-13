/**
 * AI 专业词汇生成 · 术语合并规则单元测试
 *
 * 这些用例钉住的是「生成结果不能破坏用户已填内容」这条底线：
 * 生成失败、返回空、返回重复词时，原术语框必须原样可用。
 *
 * 运行：npm run test（Node 内置 test runner + tsx，无需额外依赖）
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'

import { mergeTerms } from '../src/lib/termApi.ts'

test('追加不覆盖：保留用户已填内容与原顺序', () => {
  const current = 'gradient descent, 反向传播'
  const merged = mergeTerms(current, ['特征向量', '损失函数'])

  assert.equal(merged, 'gradient descent, 反向传播, 特征向量, 损失函数')
  assert.ok(merged.startsWith(current), '原内容必须原样保留在前面')
})

test('去重：大小写不敏感，同批重复只保留第一次', () => {
  assert.equal(
    mergeTerms('Gradient, 反向传播', ['gradient', 'GRADIENT', 'Backprop']),
    'Gradient, 反向传播, Backprop',
  )
})

test('空值/空白：不生成空条目或多余分隔符', () => {
  assert.equal(mergeTerms('', ['  ', '', '术语']), '术语')
  assert.equal(mergeTerms('   ', []), '')
  assert.equal(mergeTerms('a,,  ,b', ['', ' ']), 'a, b')
})

test('中英文逗号混合：统一用英文逗号 + 空格连接', () => {
  assert.equal(mergeTerms('a，b, c', ['d']), 'a, b, c, d')
  assert.equal(mergeTerms('甲，乙，', ['丙']), '甲, 乙, 丙')
})

test('全部重复：不清空原文，也不追加重复项', () => {
  assert.equal(mergeTerms('A, B', ['a', 'b']), 'A, B')
})