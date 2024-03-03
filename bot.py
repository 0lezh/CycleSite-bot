import os, discord, asyncio, sqlite3, sys, time, socket, requests, asyncio, warnings
from discord import app_commands, Color, ui, utils
from discord.ext import tasks
from icecream import ic
from random import randint, choice
from humanfriendly import parse_timespan, InvalidTimespan
from datetime import datetime, timedelta
from openai import OpenAI
from discord_webhook import DiscordWebhook, DiscordEmbed

#Сегодня без монолита(
import data.config as config
from data.emojis import emojis
from data.ai_utils import api_status, fetch_models, generate_response
from data.tickets_utils import ticket_launcher, ticket_operator
from data.logging import debug, info, warning, error
from colorama import Fore, Back, Style, init

#Инициализация бота
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.presences = True
client = discord.Client(intents=discord.Intents.all())
tree = app_commands.CommandTree(client)

active_model = 'gpt-3.5-turbo'

#Добавление автора к embed
def interaction_author(embed: discord.Embed, interaction: discord.Interaction):
    embed.set_author(name=interaction.user.name, icon_url=interaction.user.display_avatar)
    return embed

#Пересоздание таблицы
async def drop_table_confirmed(table, original_intrct, intrct):
    match table:
        case 'warns':
            connection = sqlite3.connect(f'data/warns.db')
            cursor = connection.cursor()
            cursor.execute(f'DROP TABLE IF EXISTS warns')
            cursor.execute(f'CREATE TABLE warns (warn_id INTEGER PRIMARY KEY, name TEXT NOT NULL, reason TEXT, message TEXT, lapse_time INTEGER)')
            cursor.execute(f'INSERT INTO warns VALUES (0, "none", "none", "none", 0)')
            embed = discord.Embed(title=f'Таблица варнов была успешно сброшена!', color=config.info)
            interaction_author(embed, original_intrct)
            connection.commit()
            connection.close()

    if not "embed" in locals():
        embed = discord.Embed(title=f'Таблицы не существует, и существовать не должно 😠', color=config.danger)
    await intrct.response.send_message(embed=embed, ephemeral = True)
    await original_intrct.delete_original_response()

#Перевод даты в unix (секунды)
def unix_datetime(source):
    return int(time.mktime(source.timetuple()))

#Мут. Жестоко и сердито.
async def mute(intrct, target, timespan):
    try:
        real_timespan = parse_timespan(timespan)
    except InvalidTimespan:
        print(f'Не удалось распарсить {timespan}')
        return
    
    #Корень зла
    try:
        await target.timeout(datetime.now().astimezone() + timedelta(seconds=real_timespan))
    except:
        embed = discord.Embed(title=f'Не удалось замутить пользователя😨', color=config.danger)
        await intrct.channel.send(embed = embed)
        return
    
    embed = discord.Embed(title=f'Пользователь был замьючен.', description=f'Он сможет снова говорить <t:{unix_datetime(datetime.now().astimezone() + timedelta(seconds=real_timespan))}:R>', color=config.warning)
    await intrct.channel.send(embed = embed)


class drop_confirm(discord.ui.View):
    def __init__(self, table, intrct) -> None:
        self.table = table
        self.intrct = intrct
        super().__init__(timeout=None)

    @discord.ui.button(label="Жми! Жми! Жми!", style=discord.ButtonStyle.red, custom_id="drop_confirm")
    async def drop(self, interaction, button):
        await drop_table_confirmed(self.table, self.intrct, interaction)


#Изменение статуса
@tasks.loop(seconds = 60)
async def presence():
    emoji = choice(emojis)
    online_members = [member for member in client.get_guild(1122085072577757275).members if not member.bot and member.status == discord.Status.online]
    if online_members:
        activity_text = f'{choice(online_members).display_name} {emoji}'
        await client.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name=activity_text))


#Удаление предупреждений
@tasks.loop(hours = 1)
async def lapse_of_warns():
    connection = sqlite3.connect('data/warns.db')
    cursor = connection.cursor()
    cursor.execute('SELECT warn_id, lapse_time FROM warns')
    warns = cursor.fetchall()
    warns.pop(0)
    for warn in warns:
        if unix_datetime(datetime.now()) >= warn[1]:
            cursor.execute(f'DELETE FROM warns WHERE warn_id = {warn[0]}')
    connection.commit()
    connection.close()


#Подгрузка view с тикетами
@client.event
async def setup_hook():
    client.add_view(ticket_launcher.question())
    client.add_view(ticket_launcher.bug())
    client.add_view(ticket_launcher.report())
    client.add_view(ticket_launcher.application())
    client.add_view(ticket_operator())

#Запуск циклов и инфо о запуске
@client.event
async def on_ready():
    try:
        presence.start()
        lapse_of_warns.start()
    except RuntimeError:
        warning('Задача запущенна и не завершена')
    await tree.sync(guild=discord.Object(id=config.guild))
    info(f'{Fore.CYAN}{client.user.name}{Style.RESET_ALL} подключён к серверу!')

#Пинг бота по slash-комманде
@tree.command(name="пинг", description="Пингани бота!", guild=discord.Object(id=config.guild))
async def on_ping(intrct):
    embed = discord.Embed(title="Понг!    ", description=f"{round(client.latency * 1000)}мс", color=config.info)
    await intrct.response.send_message(embed=embed)

@client.event 
async def on_message(message):
    #Случайные реакции
    if message.author == client.user:
        return
    if randint(0, 20) == 1:
        if message.channel.category_id not in config.very_serious_categories:
            await message.add_reaction(choice(message.guild.emojis))

    #Чат гпт
    if message.mentions and int(client.user.mention.replace(f'<@{client.user.id}>', str(client.user.id))) == message.mentions[0].id and message.channel.id in config.ai_channels:
        if api_status.status_code != 200:
            await message.add_reaction("❎")
            embed = discord.Embed(title='Невозможно подключится к API', description=f'**{api_status.status_code}: {api_status.reason}**', color=config.warning)
            await message.channel.send(embed = embed, delete_after = 15)
        else:
            await message.add_reaction("☑")
            for mention in message.mentions:
                    message.content = message.content.replace(f'<@{mention.id}>', f'{mention.display_name}')
            async with message.channel.typing():
                response = generate_response(message.content, model=active_model)
                if type(response) == int:
                    await message.add_reaction("🚫")
                    embed = discord.Embed(description=f'**Ошибка {response}**', color=config.danger)
                    await message.channel.send(embed = embed, delete_after = 15)
                else:
                    await message.channel.send(response)

#Выдача и удаление роли Меценат за буст
@client.event
async def on_member_update(before, after):
    if len(before.roles) < len(after.roles):
        new_role = next(role for role in after.roles if role not in before.roles)
        if new_role.id == config.nitro_booster_id:
            await after.add_roles(client.get_guild(int(config.guild)).get_role(1138436827909455925))
    elif len(before.roles) > len(after.roles):
        old_role = next(role for role in before.roles if role not in after.roles)
        if old_role.id == config.nitro_booster_id:
            await after.remove_roles(client.get_guild(int(config.guild)).get_role(1138436827909455925))

@tree.command(name="тикет", description="Запускает систему тикетов в текущей категории!", guild=discord.Object(id=config.guild))
async def ticketing(intrct, title: str, description: str, type: str):
    match type.lower():
        case 'вопрос':
            embed = discord.Embed(title=title, description=description, color=config.info)
            await intrct.channel.send(embed=embed, view=ticket_launcher.question())
            client.add_view(ticket_launcher.question())
        case 'баг':
            embed = discord.Embed(title=title, description=description, color=config.danger)
            await intrct.channel.send(embed=embed, view=ticket_launcher.bug())
            client.add_view(ticket_launcher.bug())
        case 'жалоба':
            embed = discord.Embed(title=title, description=description, color=config.warning)
            await intrct.channel.send(embed=embed, view=ticket_launcher.report())
            client.add_view(ticket_launcher.report())
        case 'заявка':
            embed = discord.Embed(title=title, description=description, color=config.info)
            await intrct.channel.send(embed=embed, view=ticket_launcher.application())
            client.add_view(ticket_launcher.application())
    await interaction.response.defer()

@tree.command(name="выебать", description="Для MAO", guild=discord.Object(id=config.guild))
async def on_sex(intrct):
    sex_variants = [f'О, да, {intrct.user.display_name}! Выеби меня полностью, {intrct.user.display_name} 💕','Боже мой, как сильно... 💘','Ещеее! Ещееееее! 😍',f'{intrct.user.display_name}, я люблю тебя!']
    embed = discord.Embed(title = choice(sex_variants),description='', color = config.info)
    await intrct.response.send_message(embed = embed)

@tree.command(name='ретрансляция', description='Сказать от имени бота', guild=discord.Object(id=config.guild))
async def say(intrct, message: str):
    if intrct.user.id in config.bot_engineers:
        await intrct.channel.send(message)
        await intrct.response.defer()
        await intrct.delete_original_response()
    else:
        await intrct.response.send_message('У тебя нет прав.', ephemeral=True)

@tree.command(name="8ball", description="Погадаем~", guild=discord.Object(id=config.guild))
async def magic_ball(intrct):
    variants = ['Это точно.',
             'Без сомнения.',
             'Да, безусловно.',
             'Вы можете положиться на него.',
             'На мой взгляд, да.',
             'Вероятно.',
             'Перспективы хорошие.',
             'Да.',
             'Знаки указывают на да.',
             'Ответ неясен, попробуйте еще раз.',
             'Спросите позже.',
             'Лучше не говорить тебе сейчас.',
             'Сейчас предсказать невозможно.',
             'Сосредоточьтесь и спросите еще раз.',
             'Не рассчитывай на это.',
             'Мой ответ — нет.',
             'Мои источники говорят нет.',
             'Перспективы не очень хорошие.',
             'Очень сомнительно.']
    embed = discord.Embed(title = choice(variants), color = config.info)
    await intrct.response.send_message(embed = embed)

@tree.command(name='дроп', description='Сбросить таблицу', guild=discord.Object(id=config.guild))
async def drop(intrct, table: str):
    if intrct.user.id not in config.bot_engineers:
        await intrct.response.send_message('У тебя нет прав.', ephemeral=True)
        return
    embed = discord.Embed(title="Ты точно хочешь сбросить таблицу?", description=f"Будет сброшена таблица {table} у {socket.gethostname()}", color=config.danger)
    await intrct.response.send_message(embed = embed, view = drop_confirm(table, intrct), ephemeral = True, delete_after = 15)
    
@tree.command(name="варн", description="Выдача предупреждения", guild=discord.Object(id=config.guild))
async def warn(intrct, user: discord.Member, reason: str):
    connection = sqlite3.connect('data/warns.db')
    cursor = connection.cursor()
    if user.id == client.user.id:
        await intrct.response.send_message("Нет.", ephemeral=True)
        return
    if user.bot == 1:
        await intrct.response.send_message("Ты не можешь выдать предупреждение боту.", ephemeral=True)
        return
    if user == intrct.user:
        await intrct.response.send_message("Попроси кого-нибудь другого.", ephemeral=True)
        return
    dt_string = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    cursor.execute('SELECT max(warn_id) FROM warns')
    case_id = cursor.fetchone()[0] + 1
    embed = discord.Embed(
            title=f"Выдано предупреждение!",
            description=f'Пользователь {user.mention} получил предупреждение \nСлучай {case_id}',
            color=config.info
        )
    interaction_author(embed, intrct)
    embed.add_field(
            name="Причина:",
            value=reason,
            inline=False
        )
    embed.add_field(
            name="Истекает:",
            value=f"<t:{unix_datetime(datetime.now() + timedelta(days=30))}:f>",
        )
    await intrct.response.send_message(embed=embed)
    await intrct.guild.get_channel(config.warns_log_channel).send(embed = embed)
    response = await intrct.original_response()
    cursor.execute('INSERT INTO warns (name, reason, message, lapse_time) VALUES (?, ?, ?, ?)', (user.mention, reason, response.jump_url, unix_datetime(datetime.now() + timedelta(days=30))))
    cursor.execute('SELECT * FROM warns WHERE name = ?', (user.mention,))
    players_warns = len(cursor.fetchall())
    match players_warns:
        case 2:
            await mute(intrct, user, '1d')
        case 3:
            await mute(intrct, user, '2d')
        case 4:
            await mute(intrct, user, '7d')
    if players_warns >= 5:
        await mute(intrct, user, '14d')
    await intrct.channel.send(user.mention)

    connection.commit()
    connection.close()

@tree.command(name="список_варнов", description="Помощь", guild=discord.Object(id=config.guild))
async def warns_list(intrct, user: discord.Member = None):
    if not user:
        user = intrct.user
    if user == client.user:
        await intrct.response.send_message("Ты не поверишь!", ephemeral=True)
        return
    connection = sqlite3.connect('data/warns.db')
    cursor = connection.cursor()
    cursor.execute('SELECT warn_id, reason, message, lapse_time FROM warns WHERE name = ?', (user.mention,))
    warns = cursor.fetchall()
    if warns:
        embed = discord.Embed(title=f'Предупреждения пользователя {user.display_name}:', color=config.warning)
        interaction_author(embed, intrct)
        for warn in warns:
            embed.add_field(
                name=f'Предупреждение {warn[0]}',
                value=f'Причина: {warn[1]}  \nСсылка: {warn[2]}   \nИстекает: <t:{warn[3]}:R>',
                inline=False
            )
        await intrct.response.send_message(embed=embed)
    else:
        embed = discord.Embed(title=f'Предупреждения пользователя {user.display_name}:', description='Предупреждений нет, но это всегда можно исправить!', color=config.info)
        interaction_author(embed, intrct)
        await intrct.response.send_message(embed=embed)
    connection.commit()
    connection.close()

@tree.command(name='снять_варн', description='Досрочно снять варн', guild=discord.Object(id=config.guild))
async def warn_del(intrct, warn_id: int):
    if warn_id > 0:
        connection = sqlite3.connect('data/warns.db')
        cursor = connection.cursor()
        cursor.execute('DELETE FROM warns WHERE warn_id = ?', (warn_id,))
        embed = discord.Embed(title=f'Варн {warn_id} был успешно снят.', color=config.info)
        interaction_author(embed, intrct)
        await intrct.response.send_message(embed=embed)
        connection.commit()
        connection.close()
    else:
        embed = discord.Embed(title='Не влезай, убьёт!', color=config.danger)
        await intrct.response.send_message(embed=embed, ephemeral=True)
    
@tree.command(name='аватар', description='Аватар пользователя', guild=discord.Object(id=config.guild))
async def avatar(intrct, user: discord.Member = None):
    if user:
        embed = discord.Embed(title=f'Аватар пользователя {user.display_name}:', color=config.info)
        embed.set_image(url=user.display_avatar.url)
        await intrct.response.send_message(embed=embed)
    else:
        embed = discord.Embed(title=f'Аватар пользователя {intrct.user.display_name}:', color=config.info)
        embed.set_image(url=intrct.user.display_avatar.url)
        await intrct.response.send_message(embed=embed)

@tree.command(name='сменить_ии', description='Сменить ИИ', guild=discord.Object(id=config.guild))
async def change_gpt_model(intrct, model: str):
    if model in fetch_models():
        global active_model
        active_model = model
        embed = discord.Embed(description=f'**ИИ был успешно изменен на {model}.**', color=config.info)
        await intrct.response.send_message(embed=embed)
    else:
        embed = discord.Embed(title='Список доступных моделей:', description='\n'.join(fetch_models()), color=config.info)
        await intrct.response.send_message(embed=embed, ephemeral=True)

@client.event
async def on_message_delete(message):

    if message.author == client.user:
        return

    attachments = ''

    if message.attachments:
        attachments_temp = []
        for attachment in message.attachments:
            attachments_temp.append(attachment.url)
        attachments = '\n'.join(attachments_temp)

    embed = discord.Embed(title="🗑️ Сообщение Удалено", color=config.info)
    embed.set_author(name=str(message.author), icon_url=str(message.author.display_avatar))
    embed.add_field(name="Отправитель", value=str(message.author.mention), inline=False)
    if message.content != '':
        embed.add_field(name="Сообщение", value=str(f"```{message.content}```" + attachments), inline=False)
    elif attachments != '':
        embed.add_field(name="Вложения", value=str(attachments), inline=False)
    embed.add_field(name="Канал", value=str(message.channel.mention), inline=False)

    if message.channel.category_id in config.very_secret_categories:
        await client.get_guild(config.guild).get_channel(config.logs_channels.private).send(embed = embed)
    else:
        await client.get_guild(config.guild).get_channel(config.logs_channels.main).send(embed = embed)

@client.event
async def on_message_edit(message_before, message_after):

    if str(message_before.content) != str(message_after.content) and str(message_after.content) != '':      
        embed = discord.Embed(title='✏️ Сообщение Отредактировано', color=config.info)
        embed.set_author(name=str(message_before.author), icon_url=str(message_before.author.display_avatar))
        embed.add_field(name="Отправитель", value=str(message_before.author.mention), inline=False)
        embed.add_field(name="До", value=str(f"```{message_before.content}```"), inline=False)
        embed.add_field(name="После", value=str(f"```{message_after.content}```"), inline=False)
        embed.add_field(name="Канал", value=str(message_after.channel.mention), inline=False)

        if message_after.channel.category_id in config.very_secret_categories:
            await client.get_guild(config.guild).get_channel(config.logs_channels.private).send(embed = embed)
        else:
            await client.get_guild(config.guild).get_channel(config.logs_channels.main).send(embed = embed)

@client.event
async def on_voice_state_update(member, state_before, state_after):

    voice_channel_before = state_before.channel
    voice_channel_after = state_after.channel

    if voice_channel_before == voice_channel_after:
        return
    
    if voice_channel_before == None:
        embed = discord.Embed(description=f'**{member.mention} присоединился к {voice_channel_after.mention}**', color=config.info)
        embed.set_author(name=member.display_name, icon_url=str(member.display_avatar))

    elif voice_channel_after == None:
        embed = discord.Embed(description=f'**{member.mention} вышел из {voice_channel_before.mention}**', color=config.info)
        embed.set_author(name=member.display_name, icon_url=str(member.display_avatar))

    else:
        embed = discord.Embed(description=f'**{member.mention} перешел из {voice_channel_before.mention} в {voice_channel_after.mention}**', color=config.info)
        embed.set_author(name=member.display_name, icon_url=str(member.display_avatar))
    
    await client.get_guild(config.guild).get_channel(config.logs_channels.voice).send(embed = embed)

@client.event
async def on_member_join(member):
        embed = discord.Embed(description=f'**✔ {member.mention} присоединился к серверу**', color=config.success)
        embed.add_field(name="Дата регистрации", value=f'<t:{unix_datetime(member.created_at)}:f>', inline=False)
        embed.set_thumbnail(url = str(member.display_avatar))
        
        await client.get_guild(config.guild).get_channel(config.logs_channels.main).send(embed = embed)

@client.event
async def on_member_remove(member):
        embed = discord.Embed(description=f'**🔻 {member.mention} покинул сервер**', color=config.danger)
        embed.add_field(name="Дата присоединения", value=f'<t:{unix_datetime(member.joined_at)}:f>', inline=False)
        embed.set_thumbnail(url = str(member.display_avatar))

        await client.get_guild(config.guild).get_channel(config.logs_channels.main).send(embed = embed)

client.run(config.token)
