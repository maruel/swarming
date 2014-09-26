# Copyright 2013 The Swarming Authors. All rights reserved.
# Use of this source code is governed by the Apache v2.0 license that can be
# found in the LICENSE file.

"""Main entry point for Swarming service.

This file contains the URL handlers for all the Swarming service URLs,
implemented using the webapp2 framework.
"""

import collections
import datetime
import itertools
import os

import webapp2

from google.appengine.api import users
from google.appengine.datastore import datastore_query
from google.appengine.ext import ndb

import handlers_api
import handlers_backend
import template
from components import auth
from components import ereporter2
from components import utils
from server import acl
from server import bot_management
from server import file_chunks
from server import stats_gviz
from server import task_common
from server import task_result
from server import task_scheduler
from server import user_manager


ROOT_DIR = os.path.dirname(os.path.abspath(__file__))


# Helper class for displaying the sort options in html templates.
SortOptions = collections.namedtuple('SortOptions', ['key', 'name'])


### is_admin pages.

# TODO(maruel): Sort the handlers once they got their final name.


class UploadStartSlaveHandler(auth.AuthenticatingHandler):
  """Accept a new start slave script."""

  @auth.require(acl.is_admin)
  def get(self):
    params = {
      'content': bot_management.get_start_slave(),
      'path': self.request.path,
      'xsrf_token': self.generate_xsrf_token(),
    }
    self.response.out.write(
        template.render('swarming/restricted_uploadstartslave.html', params))

  @auth.require(acl.is_admin)
  def post(self):
    script = self.request.get('script', '')
    if not script:
      self.abort(400, 'No script uploaded')

    bot_management.store_start_slave(script.encode('utf-8', 'replace'))
    self.get()


class UploadBootstrapHandler(auth.AuthenticatingHandler):
  @auth.require(acl.is_admin)
  def get(self):
    params = {
      'content': bot_management.get_bootstrap(self.request.host_url),
      'path': self.request.path,
      'xsrf_token': self.generate_xsrf_token(),
    }
    self.response.out.write(
        template.render('swarming/restricted_uploadbootstrap.html', params))

  @auth.require(acl.is_admin)
  def post(self):
    script = self.request.get('script', '')
    if not script:
      self.abort(400, 'No script uploaded')

    file_chunks.StoreFile('bootstrap.py', script.encode('utf-8'))
    self.get()


class WhitelistIPHandler(auth.AuthenticatingHandler):
  @auth.require(acl.is_admin)
  def get(self):
    display_whitelists = sorted(
        (
          {
            'ip': w.ip,
            'key': w.key.id,
            'url': self.request.path_url,
          } for w in user_manager.MachineWhitelist().query()),
        key=lambda x: x['ip'])

    params = {
      'post_url': self.request.path_url,
      'whitelists': display_whitelists,
      'xsrf_token': self.generate_xsrf_token(),
    }
    self.response.out.write(
        template.render('swarming/restricted_whitelistip.html', params))

  @auth.require(acl.is_admin)
  def post(self):
    ip = self.request.get('i', self.request.remote_addr)
    mask = 32
    if '/' in ip:
      ip, mask = ip.split('/', 1)
      mask = int(mask)
    ips = acl.expand_subnet(ip, mask)

    add = self.request.get('a')
    if add == 'True':
      for ip in ips:
        user_manager.AddWhitelist(ip)
    elif add == 'False':
      for ip in ips:
        user_manager.DeleteWhitelist(ip)
    else:
      self.abort(400, 'Invalid \'a\' parameter.')
    self.get()


### acl.is_privileged_user pages.


class BotsListHandler(auth.AuthenticatingHandler):
  """Presents the list of known bots."""
  ACCEPTABLE_BOTS_SORTS = {
    'last_seen': 'Last Seen',
    'hostname': 'Hostname',
    '__key__': 'ID',
  }
  SORT_OPTIONS = [
    SortOptions(k, v) for k, v in sorted(ACCEPTABLE_BOTS_SORTS.iteritems())
  ]

  @auth.require(acl.is_privileged_user)
  def get(self):
    limit = int(self.request.get('limit', 100))
    cursor = datastore_query.Cursor(urlsafe=self.request.get('cursor'))
    sort_by = self.request.get('sort_by', '__key__')
    if sort_by not in self.ACCEPTABLE_BOTS_SORTS:
      self.abort(400, 'Invalid sort_by query parameter')

    order = datastore_query.PropertyOrder(
        sort_by, datastore_query.PropertyOrder.ASCENDING)

    now = utils.utcnow()
    cutoff = now - bot_management.BOT_DEATH_TIMEOUT

    num_total_bots_future = bot_management.Bot.query().count_async()
    num_dead_bots_future = bot_management.Bot.query(
        bot_management.Bot.last_seen_ts < cutoff).count_async()
    fetch_future = bot_management.Bot.query().order(order).fetch_page_async(
        limit, start_cursor=cursor)

    # TODO(maruel): self.request.host_url should be the default AppEngine url
    # version and not the current one. It is only an issue when
    # version-dot-appid.appspot.com urls are used to access this page.
    version = bot_management.get_slave_version(self.request.host_url)
    bots, cursor, more = fetch_future.get_result()
    # Prefetch the tasks. We don't actually use the value here, it'll be
    # implicitly used by ndb local's cache when refetched by the html template.
    tasks = filter(None, (b.task for b in bots))
    ndb.get_multi(tasks)
    num_total_bots = num_total_bots_future.get_result()
    num_dead_bots = num_dead_bots_future.get_result()
    params = {
      'bots': bots,
      'current_version': version,
      'cursor': cursor.urlsafe() if cursor and more else '',
      'is_admin': acl.is_admin(),
      'is_privileged_user': acl.is_privileged_user(),
      'limit': limit,
      'now': now,
      'num_bots_alive': num_total_bots - num_dead_bots,
      'num_bots_dead': num_dead_bots,
      'sort_by': sort_by,
      'sort_options': self.SORT_OPTIONS,
    }
    self.response.out.write(
        template.render('swarming/restricted_botslist.html', params))


class BotHandler(auth.AuthenticatingHandler):
  @auth.require(acl.is_privileged_user)
  def get(self, bot_id):
    limit = int(self.request.get('limit', 100))
    cursor = datastore_query.Cursor(urlsafe=self.request.get('cursor'))
    bot_future = bot_management.get_bot_key(bot_id).get_async()
    run_results, cursor, more = task_result.TaskRunResult.query(
        task_result.TaskRunResult.bot_id == bot_id).order(
            -task_result.TaskRunResult.started_ts).fetch_page(
                limit, start_cursor=cursor)
    now = utils.utcnow()
    bot = bot_future.get_result()
    # Calculate the time this bot was idle.
    idle_time = datetime.timedelta()
    run_time = datetime.timedelta()
    if run_results:
      run_time = run_results[0].duration_now() or datetime.timedelta()
      if not cursor and run_results[0].state != task_result.State.RUNNING:
        # Add idle time since last task completed. Do not do this when a cursor
        # is used since it's not representative.
        idle_time = now - run_results[0].ended_ts
      for index in xrange(1, len(run_results)):
        # .started_ts will always be set by definition but .ended_ts may be None
        # if the task was abandoned. We can't count idle time since the bot may
        # have been busy running *another task*.
        # TODO(maruel): One option is to add a third value "broken_time".
        # Looking at timestamps specifically could help too, e.g. comparing
        # ended_ts of this task vs the next one to see if the bot was assigned
        # two tasks simultaneously.
        if run_results[index].ended_ts:
          idle_time += (
              run_results[index-1].started_ts - run_results[index].ended_ts)
          duration = run_results[index].duration
          if duration:
            run_time += duration

    params = {
      'bot': bot,
      'bot_id': bot_id,
      'current_version':
          bot_management.get_slave_version(self.request.host_url),
      'cursor': cursor.urlsafe() if cursor and more else None,
      'idle_time': idle_time,
      'is_admin': acl.is_admin(),
      'limit': limit,
      'now': now,
      'run_results': run_results,
      'run_time': run_time,
    }
    # TODO(maruel): Make the delete link redirect to /restricted/bots. It would
    # probably be preferable to not use /delete_machine_stats and create a UI
    # specialized endpoint instead.
    self.response.out.write(
        template.render('swarming/restricted_bot.html', params))


### User accessible pages.


class TasksHandler(auth.AuthenticatingHandler):
  """Lists all requests and allows callers to manage them."""
  # Each entry is an item in the Sort column.
  # Each entry is (key, text, hover)
  SORT_CHOICES = [
    ('created_ts', 'Created', 'Most recently created tasks are shown first.'),
    ('modified_ts', 'Active',
      'Shows the most recently active tasks first. Using this order resets '
      'state to \'All\'.'),
    ('completed_ts', 'Completed',
      'Shows the most recently completed tasks first. Using this order resets '
      'state to \'All\'.'),
    ('abandoned_ts', 'Abandoned',
      'Shows the most recently abandoned tasks first. Using this order resets '
      'state to \'All\'.'),
  ]

  # Each list is one column in the Task state filtering column.
  # Each sublist is the checkbox item in this column.
  # Each entry is (key, text, hover)
  # TODO(maruel): Evaluate what the categories the users would like for
  # diagnosis, then adapt the DB to enable efficient queries.
  STATE_CHOICES = [
    [
      ('all', 'All', 'All tasks ever requested independent of their state.'),
      ('pending', 'Pending',
        'Tasks that are still ready to be assigned to a bot. Using this order '
        'resets order to \'Created\'.'),
      ('running', 'Running',
        'Tasks being currently executed by a bot. Using this order resets '
        'order to \'Created\'.'),
      ('pending_running', 'Pending|running',
        'Tasks either \'pending\' or \'running\'. Using this order resets '
        'order to \'Created\'.'),
    ],
    [
      ('completed', 'Completed',
        'All tasks that are completed, independent if the task itself '
        'succeeded or failed. This excludes tasks that had an infrastructure '
        'failure. Using this order resets order to \'Created\'.'),
      ('completed_success', 'Successes',
        'Tasks that completed successfully. Using this order resets order to '
        '\'Created\'.'),
      ('completed_failure', 'Failures',
        'Tasks that were executed successfully but failed, e.g. exit code is '
        'non-zero. Using this order resets order to \'Created\'.'),
      # TODO(maruel): This is never set until the new bot API is writen.
      # https://code.google.com/p/swarming/issues/detail?id=117
      #('timed_out', 'Execution timed out'),
    ],
    [
      ('bot_died', 'Bot died',
        'The bot stopped sending updates while running the task, causing the '
        'task execution to time out. This is considered an infrastructure '
        'failure and the usual reason is that the bot BSOD\'ed or '
        'spontaneously rebooted. Using this order resets order to '
        '\'Created\'.'),
      ('expired', 'Expired',
        'The task was not assigned a bot until its expiration timeout, causing '
        'the task to never being assigned to a bot. This can happen when the '
        'dimension filter was not available or overloaded with a low priority. '
        'Either fix the priority or bring up more bots with these dimensions. '
        'Using this order resets order to \'Created\'.'),
      ('canceled', 'Canceled',
        'The task was explictly canceled by a user before it started '
        'executing. Using this order resets order to \'Created\'.'),
    ],
  ]

  @auth.require(acl.is_user)
  def get(self):
    """Handles both ndb.Query searches and search.Index().search() queries.

    If |task_name| is set or not affects the meaning of |cursor|. When set, the
    cursor is for search.Index, otherwise the cursor is for a ndb.Query.
    """
    cursor_str = self.request.get('cursor')
    limit = int(self.request.get('limit', 100))
    sort = self.request.get('sort', self.SORT_CHOICES[0][0])
    state = self.request.get('state', self.STATE_CHOICES[0][0][0])
    task_name = self.request.get('task_name', '').strip()

    if not any(sort == i[0] for i in self.SORT_CHOICES):
      self.abort(400, 'Invalid sort')
    if not any(any(state == i[0] for i in j) for j in self.STATE_CHOICES):
      self.abort(400, 'Invalid state')

    if sort != 'created_ts':
      # Zap all filters in this case to reduce the number of required indexes.
      # Revisit according to the user requests.
      state = 'all'

    now = utils.utcnow()
    counts_future = self._get_counts_future(now)

    # This call is synchronous.
    tasks, cursor_str, sort, state = self._get_tasks(
        task_name, cursor_str, limit, sort, state)

    # Prefetch the TaskRequest all at once, so that ndb's in-process cache has
    # it instead of fetching them one at a time indirectly when using
    # TaskResultSummary.request_key.get().
    futures = ndb.get_multi_async(t.request_key for t in tasks)

    # Evaluate the counts to print the filtering columns with the associated
    # numbers.
    state_choices = self._get_state_choices(counts_future)

    # Do not let dangling futures linger around.
    ndb.Future.wait_all(futures)
    params = {
      'cursor': cursor_str,
      'is_admin': acl.is_admin(),
      'is_privileged_user': acl.is_privileged_user(),
      'limit': limit,
      'now': now,
      'sort': sort,
      'sort_choices': self.SORT_CHOICES,
      'state': state,
      'state_choices': state_choices,
      'tasks': tasks,
      'task_name': task_name,
      'xsrf_token': self.generate_xsrf_token(),
    }
    # TODO(maruel): If admin or if the user is task's .user, show the Cancel
    # button. Do not show otherwise.
    self.response.out.write(template.render('swarming/user_tasks.html', params))

  def _get_tasks(self, task_name, cursor_str, limit, sort, state):
    """Returns all tasks for this query. This function is synchronous."""
    if task_name:
      # Word based search. Override the flags.
      sort = 'created_ts'
      state = 'all'
      tasks, cursor_str = task_scheduler.search_by_name(
          task_name, cursor_str, limit)
    else:
      # Normal listing.
      queries, query = self._get_query(sort, state)
      if queries:
        # When multiple queries are used, we can't use a cursor.
        cursor_str = None

        # Take the first |limit| items for each query. This is not efficient,
        # worst case is fetching N * limit entities.
        futures = [q.fetch_async(limit) for q in queries]
        lists = sum((f.get_result() for f in futures), [])
        tasks = sorted(lists, key=lambda i: i.created_ts, reverse=True)[:limit]
      else:
        # Normal efficient behavior.
        cursor = datastore_query.Cursor(urlsafe=cursor_str)
        tasks, cursor, more = query.fetch_page(limit, start_cursor=cursor)
        cursor_str = cursor.urlsafe() if cursor and more else None

    return tasks, cursor_str, sort, state

  def _get_counts_future(self, now):
    """Returns all the counting futures in parallel."""
    counts_future = {}
    last_24h = now - datetime.timedelta(days=1)
    for state_key, _, _ in itertools.chain.from_iterable(self.STATE_CHOICES):
      _, query = self._get_query(None, state_key)
      if query:
        counts_future[state_key] = query.filter(
            task_result.TaskResultSummary.created_ts >= last_24h).count_async()
    return counts_future

  def _get_state_choices(self, counts_future):
    """Converts STATE_CHOICES with _get_counts_future() into nice text."""
    # Appends the number of tasks for each filter. It gives a sense of how much
    # things are going on.
    counts = {k: v.get_result() for k, v in counts_future.iteritems()}
    state_choices = []
    for choice_list in self.STATE_CHOICES:
      state_choices.append([])
      for state_key, name, title in choice_list:
        if state_key in counts:
          name += ' (%d)' % counts[state_key]
        elif state_key == 'pending_running':
          name += ' (%d)' % (counts['pending'] + counts['running'])
        state_choices[-1].append((state_key, name, title))
    return state_choices

  def _get_query(self, sort, state):
    """Returns one or many TaskResultSummary queries."""
    query = task_result.TaskResultSummary.query()
    if sort:
      order = datastore_query.PropertyOrder(
          sort, datastore_query.PropertyOrder.DESCENDING)
      query = query.order(order)

    if state == 'pending':
      return None, query.filter(
          task_result.TaskResultSummary.state == task_result.State.PENDING)

    if state == 'running':
      return None, query.filter(
          task_result.TaskResultSummary.state == task_result.State.RUNNING)

    if state == 'pending_running':
      # This is a special case that sends two concurrent queries under the hood.
      # ndb.OR() doesn't work when order() is used, it requires __key__ sorting.
      # This is not efficient, so the DB should be updated accordingly to be
      # able to support pagination.
      queries = [
        query.filter(
            task_result.TaskResultSummary.state == task_result.State.PENDING),
        query.filter(
            task_result.TaskResultSummary.state == task_result.State.RUNNING),
      ]
      return queries, None

    if state == 'completed':
      return None, query.filter(
          task_result.TaskResultSummary.state == task_result.State.COMPLETED)

    if state == 'completed_success':
      query = query.filter(
          task_result.TaskResultSummary.state == task_result.State.COMPLETED)
      return None, query.filter(task_result.TaskResultSummary.failure == False)

    if state == 'completed_failure':
      query = query.filter(
          task_result.TaskResultSummary.state == task_result.State.COMPLETED)
      return None, query.filter(task_result.TaskResultSummary.failure == True)

    if state == 'expired':
      return None, query.filter(
          task_result.TaskResultSummary.state == task_result.State.EXPIRED)

    # TODO(maruel): This is never set until the new bot API is writen.
    # https://code.google.com/p/swarming/issues/detail?id=117
    if state == 'timed_out':
      return None, query.filter(
          task_result.TaskResultSummary.state == task_result.State.TIMED_OUT)

    if state == 'bot_died':
      return None, query.filter(
          task_result.TaskResultSummary.state == task_result.State.BOT_DIED)

    if state == 'canceled':
      return None, query.filter(
          task_result.TaskResultSummary.state == task_result.State.CANCELED)

    if state == 'all':
      return None, query

    self.abort(400, 'invalid state')


class TaskHandler(auth.AuthenticatingHandler):
  """Show the full text of a test request."""

  @auth.require(acl.is_user)
  def get(self, key_id):
    key = None
    request_key = None
    try:
      key = task_scheduler.unpack_result_summary_key(key_id)
      request_key = task_result.result_summary_key_to_request_key(key)
    except ValueError:
      try:
        key = task_scheduler.unpack_run_result_key(key_id)
        request_key = task_result.result_summary_key_to_request_key(
            task_result.run_result_key_to_result_summary_key(key))
      except (NotImplementedError, ValueError):
        self.abort(404, 'Invalid key format.')

    # 'result' can be either a TaskRunResult or TaskResultSummary.
    result, request = ndb.get_multi([key, request_key])
    if not result:
      self.abort(404, 'Invalid key.')

    if not acl.is_privileged_user():
      self.abort(403, 'Implement access control based on the user')

    bot_id = result.bot_id
    following_task_future = None
    previous_task_future = None
    if result.started_ts:
      # Use a shortcut name because it becomes unwieldy otherwise.
      cls = task_result.TaskRunResult

      # Note that the links will be to the TaskRunResult, not to
      # TaskResultSummary.
      following_task_future = cls.query(
          cls.bot_id == bot_id,
          cls.started_ts > result.started_ts,
          ).order(cls.started_ts).get_async()
      previous_task_future = cls.query(
          cls.bot_id == bot_id,
          cls.started_ts < result.started_ts,
          ).order(-cls.started_ts).get_async()

    bot_future = (
        bot_management.get_bot_key(bot_id).get_async() if bot_id else None)

    following_task_id = None
    following_task_name = None
    previous_task_id = None
    previous_task_name = None
    if following_task_future:
      following_task = following_task_future.get_result()
      if following_task:
        following_task_id = task_common.pack_run_result_key(following_task.key)
        following_task_name = following_task.name
    if previous_task_future:
      previous_task = previous_task_future.get_result()
      if previous_task:
        previous_task_id = task_common.pack_run_result_key(previous_task.key)
        previous_task_name = previous_task.name

    params = {
      'bot': bot_future.get_result() if bot_future else None,
      'is_admin': acl.is_admin(),
      'is_gae_admin': users.is_current_user_admin(),
      'is_privileged_user': acl.is_privileged_user(),
      'following_task_id': following_task_id,
      'following_task_name': following_task_name,
      'full_appid': os.environ['APPLICATION_ID'],
      'is_running': result.state == task_result.State.RUNNING,
      'now': utils.utcnow(),
      'previous_task_id': previous_task_id,
      'previous_task_name': previous_task_name,
      'request': request,
      'task': result,
      'xsrf_token': self.generate_xsrf_token(),
    }
    self.response.out.write(template.render('swarming/user_task.html', params))


class TaskCancelHandler(auth.AuthenticatingHandler):
  """Cancel a task.

  Ensures that the associated TaskToRun is canceled and update the
  TaskResultSummary accordingly.
  """

  @auth.require(acl.is_admin)
  def post(self):
    key_id = self.request.get('task_id', '')
    try:
      key = task_scheduler.unpack_result_summary_key(key_id)
    except ValueError:
      self.abort_with_error(400, error='Invalid key')
    redirect_to = self.request.get('redirect_to', '')

    task_scheduler.cancel_task(key)
    if redirect_to == 'listing':
      self.redirect('/user/tasks')
    else:
      self.redirect('/user/task/%s' % key_id)


### Public pages.


class RootHandler(auth.AuthenticatingHandler):
  @auth.public
  def get(self):
    params = {
      'host_url': self.request.host_url,
      'is_admin': acl.is_admin(),
      'is_bot': acl.is_bot(),
      'is_privileged_user': acl.is_privileged_user(),
      'is_user': acl.is_user(),
      'user_type': acl.get_user_type(),
    }
    self.response.out.write(template.render('swarming/root.html', params))


class WarmupHandler(webapp2.RequestHandler):
  def get(self):
    auth.warmup()
    bot_management.get_swarming_bot_zip(self.request.host_url)
    utils.get_module_version_list(None, None)
    self.response.headers['Content-Type'] = 'text/plain; charset=utf-8'
    self.response.write('ok')


def create_application(debug):
  template.bootstrap()
  ereporter2.configure()

  routes = [
      # Frontend pages. They return HTML.
      # Public pages.
      ('/', RootHandler),
      ('/stats', stats_gviz.StatsSummaryHandler),
      ('/stats/dimensions/<dimensions:.+>', stats_gviz.StatsDimensionsHandler),
      ('/stats/user/<user:.+>', stats_gviz.StatsUserHandler),

      # User pages.
      ('/user/tasks', TasksHandler),
      ('/user/task/<key_id:[0-9a-fA-F]+>', TaskHandler),
      ('/user/tasks/cancel', TaskCancelHandler),

      # Privileged user pages.
      ('/restricted/bots', BotsListHandler),
      ('/restricted/bot/<bot_id:.+>', BotHandler),

      # Admin pages.
      ('/restricted/whitelist_ip', WhitelistIPHandler),
      ('/restricted/upload_start_slave', UploadStartSlaveHandler),
      ('/restricted/upload_bootstrap', UploadBootstrapHandler),

      # The new APIs:
      ('/swarming/api/v1/stats/summary/<resolution:[a-z]+>',
        stats_gviz.StatsGvizSummaryHandler),
      ('/swarming/api/v1/stats/dimensions/<dimensions:.+>/<resolution:[a-z]+>',
        stats_gviz.StatsGvizDimensionsHandler),
      ('/swarming/api/v1/stats/user/<user:.+>/<resolution:[a-z]+>',
        stats_gviz.StatsGvizUserHandler),

      ('/_ah/warmup', WarmupHandler),
  ]
  routes = [webapp2.Route(*i) for i in routes]

  # If running on a local dev server, allow bots to connect without prior
  # groups configuration. Useful when running smoke test.
  if utils.is_local_dev_server():
    acl.bootstrap_dev_server_acls()

  # TODO(maruel): Split backend into a separate module. For now add routes here.
  routes.extend(handlers_backend.get_routes())
  routes.extend(handlers_api.get_routes())
  routes.extend(ereporter2.get_frontend_routes())

  return webapp2.WSGIApplication(routes, debug=debug)
