import logging
from asyncio import sleep
from os import getenv
from sys import exit

from aiogram import Bot, Dispatcher, executor, types
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters import Text
from aiogram.contrib.fsm_storage.redis import RedisStorage2

from bot import casino, const
from bot.throttling import ThrottlingMiddleware, throttle_decorator

# Токен берётся из переменной окружения (можно задать через systemd unit)
token = getenv("BOT_TOKEN")
if not token:
    exit("Error: no token provided")

# Инициализация объектов бота, хранилища в памяти, логера и кэша (для троттлинга)
bot = Bot(token=token, parse_mode="HTML")
dp = Dispatcher(
    bot,
    storage=RedisStorage2(
        host=getenv("REDIS_HOST", "redis")
    )
)
logging.basicConfig(level=logging.INFO)


def get_spin_keyboard():
    # noinspection PyTypeChecker
    """
    Функция, возвращающая клавиатуру с единственной кнопкой "Испытать удачу!"
    """
    return types.ReplyKeyboardMarkup([[const.SPIN_TEXT]], resize_keyboard=True)


@throttle_decorator("default")
@dp.message_handler(commands="start")
async def cmd_start(message: types.Message, state: FSMContext):
    """
    Функция, обрабатывающая команду /start, которая возвращает сообщение с приветствием, 
    информацией о правилах игры, текущим счётом и кнопкой "Испытать удачу!"

    :param message: входящее сообщение в Telegram
    :param state: объект, хранящий информацию о состоянии пользователя
    """
    start_text = "Добро пожаловать в наше виртуальное казино!\n" \
                 f"У вас {const.START_POINTS} очков. Каждая попытка стоит 1 очко, а за выигрышные комбинации вы получите:\n\n" \
                 "3 одинаковых символа (кроме семёрки) — 7 очков\n" \
                 "7️⃣7️⃣▫️ — 5 очков (точка = что угодно)\n" \
                 "7️⃣7️⃣7️⃣ — 10 очков\n\n" \
                 "<b>Внимание</b>: бот предназначен исключительно для демонстрации " \
                 "и ваши данные могут быть сброшены в любой момент! Лудомания — это болезнь, " \
                 "а никаких платных опций, кроме <i>необязательного</i> доната автору, в боте нет.\n\n" \
                 "Убрать клавиатуру — /stop\n" \
                 "Показать клавиатуру, если пропала — /spin"
    await state.update_data(score=const.START_POINTS)
    await message.answer(start_text, reply_markup=get_spin_keyboard())


@throttle_decorator("default")
@dp.message_handler(commands="stop")
async def cmd_stop(message: types.Message):
    """
    Функция, обрабатывающая команду /stop, которая убирает клавиатуру
    и оставляет пользователю только возможность начать заново с помощью /start,
    или продолжить играть с помощью /spin

    :param message: входящее сообщение в Telegram
    """

    await message.answer(
        "Клавиатура удалена. Начать заново: /start, вернуть клавиатуру и продолжить: /spin",
        reply_markup=types.ReplyKeyboardRemove()
    )


@throttle_decorator("default")
@dp.message_handler(commands="help")
async def cmd_help(message: types.Message):
    """
    Функция, обрабатывающая команду /help, которая отправляет пользователю текст 
    с информацией о принципах работы казино, примере кода для получения комбинации 
    по значению от Bot API, а также ссылками на репозитории с кодом бота

    :param message: входящее сообщение в Telegram
    """
    help_text = "В казино доступно 4 элемента: BAR, виноград, лимон и цифра семь. Комбинаций, соответственно, 64. " \
                "Для распознавания комбинации используется четверичная система, а пример кода " \
                "для получения комбинации по значению от Bot API можно увидеть " \
                "<a href='https://gist.github.com/MasterGroosha/963c0a82df348419788065ab229094ac'>здесь</a>.\n\n" \
                "Исходный код бота доступен на <a href='https://git.groosha.space/shared/telegram-casino-bot'>GitLab</a> " \
                "и на <a href='https://github.com/MasterGroosha/telegram-casino-bot'>GitHub</a> (зеркало)."
    await message.answer(help_text, disable_web_page_preview=True)


@throttle_decorator("spin")
@dp.message_handler(commands="spin")
@dp.message_handler(Text(equals=const.SPIN_TEXT))
async def make_spin(message: types.Message, state: FSMContext):
    # Получение текущего счёта пользователя (или значения по умолчанию)
    """
    Функция, обрабатывающая команду /spin, которая отправляет игровой автомат,
    получает комбинацию, проверяет, является ли она выигрышной, и обновляет счёт
    пользователя. Если счёт пользователя равен нулю, посылает сообщение
    с предупреждением о нулевом балансе.

    :param message: входящее сообщение в Telegram
    :param state: объект, хранящий информацию о пользователе
    """
    user_data = await state.get_data()
    user_score = user_data.get("score", const.START_POINTS)

    if user_score == 0:
        await message.answer_sticker(sticker=const.STICKER_FAIL)
        await message.answer("Ваш баланс равен нулю. Вы можете смириться с судьбой и продолжить жить своей жизнью, "
                             "а можете нажать /start, чтобы начать всё заново. Или /stop, чтобы убрать клавиатуру.")
        return

    # Отправляем дайс и смотрим, что выпало
    msg = await message.answer_dice(emoji="🎰", reply_markup=get_spin_keyboard())
    dice_combo = casino.get_casino_values(msg.dice.value)
    if not dice_combo:
        await message.answer(f"Что-то пошло не так. Пожалуйста, попробуйте ещё раз. Проблема с dice №{msg.dice.value}")
        return

    # Проверяем, выигрышная комбинация или нет, обновляем счёт
    is_win, delta = casino.is_winning_combo(dice_combo)
    new_score = user_score + delta
    await state.update_data(score=new_score)

    # Готовим сообщение о выигрыше/проигрыше и
    score_msg = f"Вы выиграли {delta} очков!" if is_win else "К сожалению, вы не выиграли."

    # Имитируем задержку и отправляем ответ пользователю
    await sleep(const.THROTTLE_TIME_SPIN)
    await msg.reply(f"Ваша комбинация: {', '.join(dice_combo)} (№{msg.dice.value})\n{score_msg} "
                    f"Ваш счёт: <b>{new_score}</b>.")


async def set_commands(dispatcher):
    """
    Функция, устанавливающая команды бота (через setMyCommands)

    :param dispatcher: Dispatcher, который будет вызывать эту функцию
    """

    commands = [
        types.BotCommand(command="start", description="Перезапустить казино"),
        types.BotCommand(
            command="spin", description="Показать клавиатуру и сделать бросок"),
        types.BotCommand(command="stop", description="Убрать клавиатуру"),
        types.BotCommand(command="help", description="Справочная информация")
    ]
    await bot.set_my_commands(commands)


dp.middleware.setup(ThrottlingMiddleware())
executor.start_polling(dp, skip_updates=True, on_startup=set_commands)
