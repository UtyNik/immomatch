"""Состояния пошаговой анкеты пользователя."""

from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class OnboardingStates(StatesGroup):
    """Шаги онбординга в порядке прохождения диалога."""

    language = State()
    gender = State()
    first_name = State()
    last_name = State()
    city = State()
    radius = State()
    budget = State()
    rooms = State()
    sqm_min = State()
    sqm_max = State()
    household = State()
    household_type = State()
    wbs = State()
    jobcenter = State()
    employed = State()
    pets = State()
    income = State()  # необязательный шаг: можно пропустить → net_income = None
    custom_notes = State()
