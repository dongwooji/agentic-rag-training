export function appendClarification(original: string, reply: string) {
  return original + '\n\n추가 확인 답변:\n' + reply.trim()
}
