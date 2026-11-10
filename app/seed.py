"""사용자별 초기 데이터: 근무 유형 기본값, 영어 문장 50개 (IELTS 라이팅/스피킹에 쓸 만한 직접 작성 문장)."""
from datetime import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import SHIFT_LABELS, Sentence, ShiftType, User

DEFAULT_SHIFTS = {
    "DAY": (60, time(5, 30)),
    "EVENING": (120, time(9, 0)),
    "NIGHT": (60, time(15, 0)),
    "OFF": (180, time(8, 0)),
}

SENTENCES = [
    ("It is widely believed that education is the key to reducing inequality.", "교육이 불평등을 줄이는 열쇠라는 것은 널리 알려진 생각이다."),
    ("There is a growing tendency for people to work from home.", "재택근무를 하는 사람들이 늘어나는 추세다."),
    ("This issue has sparked a heated debate in recent years.", "이 문제는 최근 몇 년간 열띤 논쟁을 불러일으켰다."),
    ("While some argue that technology isolates us, others believe it connects us.", "기술이 우리를 고립시킨다고 주장하는 사람도 있지만, 연결해 준다고 믿는 사람도 있다."),
    ("From my perspective, the advantages clearly outweigh the disadvantages.", "내 관점에서는 장점이 단점보다 분명히 크다."),
    ("A case in point is the rapid rise of online learning platforms.", "대표적인 예가 온라인 학습 플랫폼의 급속한 성장이다."),
    ("This can be attributed to a combination of economic and social factors.", "이것은 경제적·사회적 요인이 복합적으로 작용한 결과로 볼 수 있다."),
    ("Governments should take decisive action to tackle air pollution.", "정부는 대기 오염 문제에 단호하게 대처해야 한다."),
    ("It goes without saying that health is more important than wealth.", "건강이 부보다 중요하다는 것은 말할 필요도 없다."),
    ("The figure rose steadily, reaching a peak of 45 percent in 2010.", "그 수치는 꾸준히 상승해 2010년에 45퍼센트로 정점을 찍었다."),
    ("By contrast, the proportion of older workers declined slightly.", "반면, 고령 근로자의 비율은 소폭 감소했다."),
    ("Overall, it is clear that urban populations grew over the period.", "전반적으로, 해당 기간 동안 도시 인구가 증가했음이 분명하다."),
    ("To some extent, I agree with this view, but not entirely.", "어느 정도는 이 견해에 동의하지만, 전적으로 그렇지는 않다."),
    ("This trend is likely to continue in the foreseeable future.", "이 추세는 가까운 미래에도 계속될 가능성이 높다."),
    ("One possible solution would be to invest more in public transport.", "한 가지 가능한 해결책은 대중교통에 더 많이 투자하는 것이다."),
    ("Children who read regularly tend to develop stronger language skills.", "꾸준히 책을 읽는 아이들은 더 뛰어난 언어 능력을 기르는 경향이 있다."),
    ("Striking a balance between work and life is becoming increasingly difficult.", "일과 삶의 균형을 맞추는 것이 점점 더 어려워지고 있다."),
    ("The benefits of regular exercise are well documented.", "규칙적인 운동의 이점은 충분히 입증되어 있다."),
    ("If this problem is left unaddressed, it could have serious consequences.", "이 문제를 방치하면 심각한 결과를 초래할 수 있다."),
    ("I would say that I am more of an early bird than a night owl.", "저는 올빼미형이라기보다는 아침형 인간에 가까운 것 같아요."),
    ("To be honest, I have never really thought about it before.", "솔직히 말하면, 그것에 대해 깊이 생각해 본 적은 없어요."),
    ("What I like most about my hometown is its relaxed atmosphere.", "제 고향에서 가장 좋은 점은 여유로운 분위기예요."),
    ("It depends on the situation, but generally speaking, I prefer to plan ahead.", "상황에 따라 다르지만, 대체로 저는 미리 계획하는 걸 좋아해요."),
    ("I find it really rewarding to help people in need.", "도움이 필요한 사람을 돕는 것은 정말 보람 있다고 느껴요."),
    ("Working irregular hours has taught me to manage my time carefully.", "불규칙한 시간에 일하면서 시간 관리를 꼼꼼히 하는 법을 배웠어요."),
    ("Consistency matters more than intensity when it comes to learning.", "배움에 있어서는 강도보다 꾸준함이 더 중요하다."),
    ("Small daily improvements add up to remarkable results over time.", "매일의 작은 발전이 쌓이면 시간이 지나 놀라운 결과가 된다."),
    ("This policy would place an unfair burden on low-income families.", "이 정책은 저소득 가정에 불공평한 부담을 지울 것이다."),
    ("Public awareness of environmental issues has increased considerably.", "환경 문제에 대한 대중의 인식이 상당히 높아졌다."),
    ("Not only does tourism create jobs, but it also promotes cultural exchange.", "관광은 일자리를 창출할 뿐만 아니라 문화 교류도 촉진한다."),
    ("It is essential that young people are taught practical life skills.", "젊은이들에게 실용적인 생활 기술을 가르치는 것은 필수적이다."),
    ("Some people claim that standardised tests do not reflect real ability.", "표준화된 시험이 실제 능력을 반영하지 못한다고 주장하는 사람들도 있다."),
    ("The data suggests a strong link between income and life expectancy.", "이 자료는 소득과 기대 수명 사이에 강한 연관성이 있음을 시사한다."),
    ("In the long run, investing in renewable energy will pay off.", "장기적으로 보면 재생 에너지에 대한 투자는 결실을 맺을 것이다."),
    ("Despite these drawbacks, the scheme has been largely successful.", "이러한 단점에도 불구하고, 그 제도는 대체로 성공적이었다."),
    ("Access to healthcare should be regarded as a basic human right.", "의료 서비스 이용은 기본적 인권으로 여겨져야 한다."),
    ("The number of visitors almost doubled between 2000 and 2020.", "방문객 수는 2000년부터 2020년 사이에 거의 두 배가 되었다."),
    ("A significant proportion of students rely on part-time jobs.", "상당수의 학생들이 아르바이트에 의존한다."),
    ("This is particularly true in rapidly developing countries.", "이는 급속히 발전하는 국가에서 특히 그렇다."),
    ("Urbanisation has brought both opportunities and challenges.", "도시화는 기회와 과제를 함께 가져왔다."),
    ("I used to find grammar boring, but now I actually enjoy it.", "예전에는 문법이 지루했는데, 지금은 오히려 재미있어요."),
    ("If I had more free time, I would definitely learn to cook.", "시간이 더 있다면, 꼭 요리를 배울 거예요."),
    ("The main reason I am studying English is to broaden my career options.", "제가 영어를 공부하는 주된 이유는 진로의 선택지를 넓히기 위해서예요."),
    ("Learning a language opens the door to a whole new way of thinking.", "언어를 배우는 것은 완전히 새로운 사고방식으로 가는 문을 연다."),
    ("Advertising has a profound influence on consumer behaviour.", "광고는 소비자 행동에 깊은 영향을 미친다."),
    ("It would be naive to assume that money alone brings happiness.", "돈만으로 행복해질 수 있다고 여기는 것은 순진한 생각일 것이다."),
    ("These measures may be costly, but they are necessary.", "이러한 조치는 비용이 많이 들 수 있지만, 필요하다."),
    ("In conclusion, both individuals and governments have a role to play.", "결론적으로, 개인과 정부 모두 해야 할 역할이 있다."),
    ("Progress is rarely a straight line, so keep going.", "발전은 직선으로 오지 않으니, 계속 나아가자."),
    ("Every hour you invest today makes tomorrow's test a little easier.", "오늘 투자한 한 시간이 내일의 시험을 조금 더 쉽게 만든다."),
]


def seed_user(db: Session, user: User) -> None:
    """새 사용자에게 기본 근무 유형과 영어 문장 50개를 만들어 준다 (이미 있으면 건너뜀)."""
    have = set(db.scalars(select(ShiftType.code).where(ShiftType.user_id == user.id)))
    for code, (minutes, send_time) in DEFAULT_SHIFTS.items():
        if code not in have:
            db.add(
                ShiftType(
                    user_id=user.id, code=code, label=SHIFT_LABELS[code], study_minutes=minutes, send_time=send_time
                )
            )
    if db.scalar(select(Sentence.id).where(Sentence.user_id == user.id).limit(1)) is None:
        for i, (text, meaning) in enumerate(SENTENCES):
            db.add(Sentence(user_id=user.id, text=text, meaning=meaning, position=i))
    db.commit()
