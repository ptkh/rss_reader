"""	Module for parsing XML format RSS feeds.
	
    <function 'rss_arg_parser'> creates <class 'ArgumentParser' object with following arguments: 
    url	, --version, --json, --date, --source, --verbose, --limit, --pdf, --html, --log
	
	<class 'Tree'> with methods for fetching and parsing XML document from provided url, caching news in database, converting result to json, html, pdf format.

    <class 'FeedParserException'> custom exception class for exception handling.

    <function 'logging_basicConfig'> for setting logging level for this module.

    """


import argparse
from http.client import HTTPResponse
import os
import logging
import re
import sys
import sqlite3
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET
from lxml import html
import pdfkit
import dateutil
import json



def rss_arg_parser():
	"""	Creates custom parser with following arguments: 
	\nurl					URL to XML format RSS feed
	\n--version			print version info
	\n--json				print result as JSON in stdout
	\n--date				outputs articles from specified date
	\n--source			outputs articles from specified source
	\n--verbose			output verbose status messages
	\n--limit				limit news topics, if provided
	\n--pdf				export result as PDF to provided destination (default=cwd)
	\n--html				export result as HTML to provided destination (default=cwd)
			"""
	parser = argparse.ArgumentParser(description='tool for parsing RSS feeds')
	parser.add_argument('url', metavar='URL', nargs='?', help='URL to XML format RSS feed')
	parser.add_argument('--version', action='store_true', help='print version info')
	parser.add_argument('--json', action='store_true', help='print result as JSON in stdout')
	parser.add_argument('--log', metavar='FILEPATH', type=str, default=None, help='sets logging level to logging.DEBUG')
	parser.add_argument('--date', type=str, nargs='?', default=None, help='outputs articles from specified date')
	parser.add_argument('--source', type=str, default=None, help='outputs articles from specified source')
	parser.add_argument('--verbose', action='store_true', help='output verbose status messages')
	parser.add_argument('--limit', help='limit news topics, if provided', type=int, nargs='?', default=-1, const=5)
	parser.add_argument('--pdf', metavar='FILEPATH', type=str, const=os.path.join(os.getcwd(), 'data/', 'cached_news.pdf'), nargs='?', help='export result as PDF to provided destination, might take time for downloading images')
	parser.add_argument('--html', metavar='FILEPATH', type=str,  const=os.path.join(os.getcwd(), 'data/', 'cached_news.html'), nargs='?', help='export result as HTML to provided destination')
	args = parser.parse_args()
	return args

def logging_basicConfig(LOGGING_LEVEL, LOG_FILEPATH):
	"""	Sets logging level according to call arguments and should be called before instantiating class Tree object
		if --log FILEPATH is specified sets logging level to INFO and creates log file in provided destination
		else sets logging level to provided LOGGING_LEVEL
	"""
	if LOG_FILEPATH is None:
		logging.basicConfig(level=LOGGING_LEVEL, encoding='utf-8')
	else:
		logging.basicConfig(level=logging.INFO, filepath=LOG_FILEPATH, encoding='utf-8')

class FeedParserException(Exception):
    """Custom Exception class for <class 'Tree'>"""


class Tree:
	"""	Contains methods for fetching and parsing XML document, processing sub-elements, setting correct working tags for different variants of tags found in different sources,

	(e.g. 
		<xml>
			<article>
				<link>...</link>
				<date>...</date>
				<title>...</title>
				<summary>...</summary>			
			</article>
		</xml>	
	or
		<xml>
			<rss>
				<item>
					<link>...</link>
					<pubDate>...</pubDate>
					<title>...</title>
					<description>...</description>			
				</item>
			</rss>
		</xml>
	where working tags are different and can't be parsed by using same 'key'.)

	Parsing starts when <class 'Tree'> object is initiated and result is an attribute - instance.list_of_articles - created as a list of 	dictionaries which contain news articles organized in key-value pairs"""


	# CONSTANTS
	URL = None
	HTML_FILEPATH = None
	PDF_FILEPATH = None
	DB_FILEPATH = None
	DB = None
	LIMIT = None
	JSON = None
	FILTER_K = None
	FILTER_V = None
	CACHE = []
	temp_html_path = os.path.join(os.getcwd(), 'data/.temp.html')
	PAGE_TITLE = None
	ARTICLE_DIVS = ''

	# working tags
	ARTICLE = None
	DESCRIPTION = None
	DATE = None
	LINK = 'link'
	TITLE = 'title'
	### different tag variants for parsing different sources
	article_tags = 'item', 'article', 'entry'	
	description_tags = 'description', 'summary'
	date_tags = 'pubdate', 'pubDate', 'published', 'updated'

	# RegEx pattern for removing tag prefix
	pattern_prefix = "\{.*\}"
	prefix_pattern = re.compile(pattern_prefix)
	### RegEx patterns for grabbing specific tags/content
	pattern_tag = "<.+?>"    
	pattern_enclosed_by_same_tag = "^<([a-z]+) *[^/]*?>((.|\n)*)</\\1>$"
	pattern_open_end_tag = "<([a-z]+) *[^/]*?>((.|\n)*)</\\1>"
	pattern_CDATA = "<!\[CDATA\[.*?\]\]>"
	pattern_p = "<p(|\s+[^>]*)>(.*?)<\/p\s*>"
	pattern_a = "<a\s*(.*)>(.*)</a>"
	pattern_href = 'href\s*=\s*"(.+?)"'
	pattern_img= 'img\s*=\s*"(.+?)"'
	# compiled patterns
	tag_pattern = re.compile(pattern_tag)
	enclosed_by_same_tag_pattern = re.compile(pattern_enclosed_by_same_tag)
	open_end_tag_pattern = re.compile(pattern_open_end_tag)
	CDATA_pattern = re.compile(pattern_CDATA)
	p_pattern = re.compile(pattern_p)
	a_pattern = re.compile(pattern_a)
	href_pattern = re.compile(pattern_href)
	img_pattern = re.compile(pattern_img)


	def __init__(self, url, json_, html_filepath, pdf_filepath, limit, filter_src, filter_date, 
					db_filepath=os.path.join(os.getcwd(),'data/cached_news.db'), ):
		"""		Initiates class <Tree> object, connects to provided url, 
		after fetching response from RSS feed website, calls get_xml_tree method and xml.etree.ElementTree(.Element) object is created,
		calls collect_descendant_elements and collects all child, grandchild and any depth child elements, calls remove_tag_prefixes method 
		(to cover situations where, while fetching, prefixes are concatenated in front of element tags by server, also collects all tags in a set and returns it)
		set_working_tags method iterates through collected tags and sets self.ARTICLE, self.DESCRIPTION, self.DATE, self.TITLE, self.LINK variables for parsing article elements later,
		collect_articles method is called, which iterates through list of child elements and collects only article elements,
		after that parse_article method is called for every article in collected articles, organizes articles and their sub-elements in dictionaries and appends them to Tree.CACHE.
		then prints (if --limit is specified limits number of articles) formatted collected news (or if --json specified converts to json) to stdout 
		or if --html or --pdf is specified converts cached news to corresponding format, after that inserts cached news in SQLite3 database.
		if URL was not provided fetches news from database (if --date or --source is specified filters before fetching)
		according to provided arguments prints to stdout or converts to specified format.
		"""
		logging.debug("Tree.__init__(%s, %s, %s, %s, %s, %s, %s, %s)" % 
					(url, json_, html_filepath, pdf_filepath, limit, filter_src, filter_date, db_filepath))
		Tree.URL = url
		Tree.HTML_FILEPATH = html_filepath
		Tree.PDF_FILEPATH = pdf_filepath
		Tree.DB_FILEPATH = db_filepath
		Tree.LIMIT = limit
		Tree.JSON = json_
		if filter_date is not None:
			Tree.FILTER_K = 'date'
			Tree.FILTER_V = filter_date
		elif filter_src is not None:
			Tree.FILTER_K = 'news_src' 
			Tree.FILTER_V = filter_src

		try:
			Tree.DB = Tree.db_connection(Tree.DB_FILEPATH)

			if Tree.URL is not None:
				logging.info(f"Tree object created. url: {url}")
				self.response = self.estimate_connection()
				logging.info(f"Connected to source. self.response: {self.response}")
				self.tree = self.get_xml_tree()
				logging.info(f"Element object created. self.tree = {self.tree}")
				self.elements = self.collect_descendant_elements()
				logging.info("All sub-elements in xml tree collected.")
				self.__tags = self.remove_tag_prefixes() 	#returns a set of tags found in xml tree
				self.set_working_tags()
				logging.info(f"Working tags set. \n\tself.ARTICLE = {self.ARTICLE}\n\tself.DESCRIPTION = {self.DESCRIPTION}\n\tself.TITLE = {self.TITLE}\n\tself.LINK = {self.LINK}")
				self.articles = self.collect_articles()
				logging.info("Article elements collected.")
				for article in self.articles:
					logging.info("Parsing article.")
					temp = self.parse_article(article)
					logging.info("Adding parsed article to cache.")
					Tree.cache_news(temp) # appends to Tree.CACHE
				if Tree.HTML_FILEPATH is None and Tree.PDF_FILEPATH is None:
					# loops through and prints cached articles
					logging.info("Printing news articles from Tree.CACHE. Tree.LIMIT = %s" % Tree.LIMIT)
					for article in Tree.CACHE:
						if Tree.LIMIT > 0:
							Tree.print_news(article)
							Tree.LIMIT -= 1
						elif Tree.LIMIT == 0:
							pass
						else:
							Tree.print_news(article)
				else:
					logging.info("Checking if --html or --pdf flags were set")
					if Tree.HTML_FILEPATH is not None:
						Tree.create_html(filepath=Tree.HTML_FILEPATH)
					if Tree.PDF_FILEPATH is not None:
						Tree.create_pdf()
			else: # if self.URL is None
				logging.info("URL not provided, fetching news from database")
				if Tree.FILTER_K is not None:
					Tree.db_fetch_news(Tree.DB, Tree.FILTER_K, Tree.FILTER_V)
				else:
					Tree.db_fetch_news(Tree.DB)
				if Tree.HTML_FILEPATH is None and Tree.PDF_FILEPATH is None:
					logging.info("Printing news articles from Tree.CACHE. Tree.LIMIT = %s" % Tree.LIMIT)
					for article in Tree.CACHE:
						if Tree.LIMIT > 0:
							Tree.print_news(article)
							Tree.LIMIT -= 1
						elif Tree.LIMIT == 0:
							pass
						else:
							Tree.print_news(article)
						if len(Tree.CACHE) < 1:
							break
				else:
					logging.info("Checking if --html or --pdf flags were set")
					if Tree.HTML_FILEPATH is not None:
						Tree.create_html(filepath=Tree.HTML_FILEPATH)
					if Tree.PDF_FILEPATH is not None:
						Tree.create_pdf()

		except FeedParserException as e:
			logging.CRITICAL(e)
			logging.exception(e)
			sys.exit(1)
		except Exception as e:
			logging.CRITICAL(e)
			logging.exception(e)
			sys.exit(1)
		finally:
			if Tree.DB is not None:
				logging.info("Database connection closed")
				Tree.DB.close()

	@staticmethod
	def estimate_connection() -> HTTPResponse:
		"""Sends request to Tree.URL and returns <http.client.HTTPResponse> object"""
		try:			
			logging.debug("Method estimate_connection called.")
			headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 6.1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/41.0.2228.0 Safari/537.3'} #overriding user-agent prevents server from blocking request
			request = Request(url=Tree.URL, headers=headers)  # 	<urllib.request.Request>
			logging.info("Request created: %s" % request)
			response = urlopen(request)
			logging.debug("Response received: %s" %response)
			
			return response        
		except Exception as e:
			logging.exception(e)
			raise FeedParserException(e)

	def get_xml_tree(self) -> ET.Element:
		"""Parses xml from <HTTPResponse>.content and returns <ElementTree.Element> object"""
		try:
			logging.debug("Method get_xml_tree called.")
			content = self.response.read()
			logging.debug("Content fetched from response: %s" % content)
			tree = ET.fromstring(content)
			logging.debug("XML Element created: %s" % tree)
			return tree
		except Exception as e:
			logging.exception(e)
			raise FeedParserException(e)

	def collect_descendant_elements(self) -> list[ET.Element]:
		"""Traverses xml tree, collects all descendant elements and returns list of <ElementTree.Element> objects"""
		try:
			logging.debug("Method collect_descendant_elements called.")
			elements = [] #list for collecting child elements
			for element in self.tree:        #(tree ~> *child* ~>...
				logging.debug("Traversing XML tree, collecting child elements")
				if element.tag == 'channel':
					for child in element:
						if child.tag == 'title':
							logging.debug("Channel title found: %s" % child.text)
							self.feed_title = child.text
				if element.tag == self.ARTICLE: #checks if child tag is item
					logging.debug("Article element found: %" % element)
					elements.append(element)    #if true, appends element block (current ET.Element object) to element list
					continue                 #and continues iterating on the same level
				for elemnt in element:       #otherwise iterates over tags one level deeper ( tree ~> child ~> *grandchild* ~> ... )
					if elemnt.tag == 'channel':
						for child in elemnt:
							if child.tag == 'title':
								logging.debug("Channel title found: %s" % child.text)
								self.feed_title = child.text
					if elemnt.tag == self.ARTICLE:
						logging.debug("Article element found: %" % element)
						elements.append(elemnt)
						continue
					for elemt in elemnt:     #( tree ~> child ~> grandchild ~> *2Xgrandchild* ~> ... )
						if elemt.tag == 'channel':
							for child in elemt:
								if child.tag == 'title':
									logging.debug("Channel title found: %s" % child.text)
									self.feed_title = child.text
						if elemt.tag == self.ARTICLE:
							logging.debug("Article element found: %" % element)
							elements.append(elemt)
							continue
						for elem in elemt:   #( tree ~> child ~> grandchild ~> 2Xgrandchild ~> *3Xgrandchild* ~> ... )
							if elem.tag == self.ARTICLE:
								logging.debug("Article element found: %" % element)
								elements.append(elem)
								continue
			logging.debug("Returning article elements: %s", elements)
			
			return elements
		except Exception as e:
			logging.exception(e)
			raise FeedParserException(e)

	def remove_tag_prefixes(self) -> set[str]:
		"""Loops through the list of descendant elements, checks if tags contain prefixes and if they do, cuts them out
		(e.g. "{'http://example.com/'}title" or "{'http://example.com/'}description",
		where "{http://example.com/'}" is prefix and following word is tag name, result is "title" and "description")
		While looping through elements, adds all found tags in a set and returns it."""
		try:
			logging.debug("Method remove_tag_prefixes called.")
			logging.info("Checking if xml tree tags contain prefixes")
			tags = set() #for collecting tags while iterating
			if hasattr(self.tree, 'tag'):    #checks if element has tag
				if re.search(Tree.prefix_pattern, self.tree.tag) is not None: # if tag has prefix, removes it
					logging.info('Removing tag prefix: %s' % element.tag)
					self.tree.tag = re.sub(Tree.prefix_pattern, '', self.tree.tag) 
				tags.add(self.tree.tag) # adds tag to set
				for element in self.tree:
					if hasattr(element, 'tag'):
						if re.search(Tree.prefix_pattern, element.tag) is not None:
							logging.info('Removing tag prefix: %s' % element.tag)
							element.tag = re.sub(Tree.prefix_pattern, '', element.tag)
						tags.add(element.tag)
						for elmnt in element:
							if hasattr(elmnt, 'tag'):
								if re.search(Tree.prefix_pattern, elmnt.tag) is not None:
									logging.info('Removing tag prefix: %s' % element.tag)
									elmnt.tag = re.sub(Tree.prefix_pattern, '', elmnt.tag)
								tags.add(elmnt.tag)
								for elmt in elmnt:
									if hasattr(elmt, 'tag'):
										if re.search(Tree.prefix_pattern, elmt.tag) is not None:
											logging.info('Removing tag prefix: %s' % element.tag)
											elmt.tag = re.sub(Tree.prefix_pattern, '', elmt.tag)
										tags.add(elmt.tag)
										for emt in elmt:
											if hasattr(emt, 'tag'):
												if re.search(Tree.prefix_pattern, emt.tag) is not None:
													logging.info('Removing tag prefix: %s' % element.tag)
													emt.tag = re.sub(Tree.prefix_pattern, '', emt.tag)
												tags.add(emt.tag)

			return tags
		except Exception as e:
			logging.exception(e)
			raise FeedParserException(e)

	def set_working_tags(self) -> None:
		"""Iterates through collected tags and sets self.ARTICLE, self.DESCRIPTION, self.DATE variables for parsing article elements to later use them while parsing article element's sub-elements."""
		try:
			logging.info("Setting working tags")
			for tag in self.__tags:
				if self.ARTICLE != None and self.DESCRIPTION != None and self.DATE != None:
					break
				if self.ARTICLE == None and tag in Tree.article_tags:
					self.ARTICLE = tag
					logging.info("Article tag set: %s" % tag)
					continue
				elif self.DESCRIPTION == None and tag in Tree.description_tags:
					self.DESCRIPTION = tag
					logging.info("Description tag set: %s" % tag)
					continue
				elif self.DATE == None and tag in Tree.date_tags:
					self.DATE = tag
					logging.info("Date tag set: %s" % tag)
					continue
		except Exception as e:
			logging.exception(e)
			raise FeedParserException(e)
		
	def collect_articles(self) -> list[ET.Element]:
		"""Loops through list of collected descendant elements and returns list of article elements"""
		try:
			articles = [] #list for collecting item blocks
			for element in self.tree:        #(tree ~> *child* ~>...
				if element.tag == self.ARTICLE: #checks if child tag is item
					logging.info("Article element found in collected articles")
					articles.append(element)    #if true, appends item block (current ET.Element object) to items list
					continue                 #and continues iterating on the same level
				for elemnt in element:       #otherwise iterates over tags one level deeper ( tree ~> child ~> *grandchild* ~> ... )
					if elemnt.tag == self.ARTICLE:
						logging.info("Article element found in collected articles")
						articles.append(elemnt)
						continue
					for elemt in elemnt:     #( tree ~> child ~> grandchild ~> *2Xgrandchild* ~> ... )
						if elemt.tag == self.ARTICLE:
							logging.info("Article element found in collected articles")
							articles.append(elemt)
							continue
						for elem in elemt:   #( tree ~> child ~> grandchild ~> 2Xgrandchild ~> *3Xgrandchild* ~> ... )
							if elem.tag == self.ARTICLE:
								logging.info("Article element found in collected articles")
								articles.append(elem)
								continue
		except Exception as e:
			logging.exception(e)
			raise FeedParserException(e)

	def parse_article(self, article: ET.Element) -> dict:
		"""Parses article sub-elements and organizes them in a dictionary
		(e.g. dict({'title': title_element_contents, 'link': link_element_contents, ...})
		returns dictionary"""
		try:
			dict_ = {}
			for element in article:
				logging.info("Parsing article sub-element: %s" % element.tag)
				if element.text is not None:
					element.text = element.text.replace(u'\xa0', u' ') 
				if element.tag == self.TITLE:
					self.parse_title(element, dict_)
				elif element.tag == self.DATE:
					self.parse_date(element, dict_)
				elif element.tag == self.LINK:
					self.parse_link(element, dict_)
				elif element.tag == self.DESCRIPTION:
					if element.text is None:
						if self.DESCRIPTION == 'description' and 'summary' in self.__tags:
							self.DESCRIPTION = 'summary'
						elif self.DESCRIPTION == 'summary' and 'description' in self.__tags:
							self.DESCRIPTION = 'description'
						continue
					self.parse_description(element, dict_)
				elif element.tag == 'content' and 'url' in element.attrib:
					if 'news_url' in dict_:
						dict_['news_url'] = f"{dict_['news_url']}\n{element.attrib['url']} (content)"
					else:
						dict_['news_url'] = f"{element.attrib['url']} (content)"
			dict_['news_title'] = dict_['news_title'].strip()
			dict_['news_url'] = dict_['news_url'].strip()
			dict_['news_src'] = Tree.URL
			if 'news_description' in dict_:
				dict_['news_description'] = dict_['news_description'].strip()
			else:
				dict_['news_description'] = ''
			if 'news_date' not in dict_:
				dict_['news_date'] = Tree.TODAY
			dict_['date'] = str(dict_['news_date'])[:10]
			dict_['news_feed_title'] = self.feed_title

			return dict_
		except Exception as e:
			logging.exception(e)
			raise FeedParserException(e)

	def parse_title(self, element: ET.Element, dict_: dict) -> None:
		"""Parses title element of xml and appends text to dict_[news_title]"""
		dict_['news_title'] = element.text
		
	def parse_date(self, element: ET.Element, dict_: dict) -> None:
		"""Parses date element of xml tree and appends datetime object to dict_[news_date]"""
		dict_['news_date'] = str(dateutil.parser.parse(element.text, ignoretz=True))

	def parse_link(self, element: ET.Element, dict_: dict) -> None:
		"""Parses link element of xml tree and appends links to dict_[news_url]"""
		if 'href' in element.attrib:
			if 'news_url' in dict_:
				dict_['news_url'] = f"{dict_['news_url']}\n{element.attrib['href']} (link)"
			else:
				dict_['news_url'] = f"{element.attrib['href']} (link)"
		elif type(element.text) == str:
			if 'http' in element.text:
				if 'news_url' in dict_:
					dict_['news_url'] = f"{dict_['news_url']}\n{element.text} (link)"
				else:
					dict_['news_url'] = f"{element.text} (link)"

	def parse_description(self, element: ET.Element, dict_: dict) -> None:
		"""Parses description element of xml tree, checks if element.text contains html fragments, accordingly parses and append text content to dict_[news_description]"""
		try:
			if 'type' in element.attrib and element.attrib['type'] == 'html':
				nodes = html.fragments_fromstring(element.text)
				self.parse_html(nodes, dict_)
			elif element.text is not None:
				if re.search(self.pattern_CDATA, element.text) != None:
					element.text = element.text[8:-3].strip()
				if re.search(self.enclosed_by_same_tag_pattern, element.text) is not None:
					s, e = re.search(self.enclosed_by_same_tag_pattern, element.text).span()
					fragment = element.text[s:e]
					nodes = html.fragments_fromstring(fragment)
					self.parse_html(nodes, dict_)
				elif re.search(self.open_end_tag_pattern, element.text) is not None:
					s,e = re.search(self.open_end_tag_pattern, element.text).span()
					text = element.text[:s]
					if re.search(self.tag_pattern, text) is not None:
						nodes = html.fragments_fromstring(text)
						self.parse_html(nodes, dict_)
					else:
						if 'news_description' in dict_:
							dict_['news_description'] = f"{dict_['news_description']}\n{text}"
						else:
							dict_['news_description'] = f"{text}"
					nodes = html.fragments_fromstring(element.text[s:e])
					self.parse_html(nodes, dict_)
				else: 
					dict_['news_description'] = element.text
		except Exception as e:
			logging.exception(e)
			raise FeedParserException(e)

	@staticmethod
	def cache_news(dict_: dict) -> None:
		"""Method for appending news articles to CACHE"""
		try:
			logging.debug("Appending news article to CACHE")
			Tree.CACHE.append(dict_)
		except Exception as e:
			logging.exception(e)
			raise FeedParserException(e)

	@staticmethod
	def print_news(article: dict) -> None:
		"""Prints formatted news item, if --json specified, prints JSON representation"""
		try:
			if Tree.JSON:
				logging.info("Printing JSON representation of article to stdout")
				json_str = Tree.convert_to_json(article)
				print(f"\n{json_str}\n")
			else:
				logging.info("Printing formatted article to stdout")
				for key, value in article.items():
					if key == 'news_feed_title':
						feed_title = value
					elif key == 'news_src':
						src = value
					elif key == 'news_title':
						title = value
					elif key == 'news_date':
						date = value
					elif key == 'news_description':
						description = value
					elif key == 'news_url':
						url = value
				print(f"""					
					\n______________________________
					\nFeed: {feed_title}
					\n______________________________
					\nSource: {src}

					\nTitle: {title}
					\nDate: {date}
					\n{description}
					\nLinks:
					\n{url}
					\n______________________________
					""")
		except Exception as e:
			logging.exception(e)
			raise FeedParserException(e)

	@staticmethod
	def create_html(filepath: str) -> None:
		"""Method for creating .html document from Tree.articles_html"""
		try:
			if filepath == Tree.temp_html_path:
				logging.info("Creating html document from Tree.CACHE >> %s" % Tree.temp_html_path)
				Tree.to_html_string(Tree.CACHE, local=True)
			else:
				logging.info("Creating html document from Tree.CACHE >> %s" % Tree.HTML_FILEPATH)
				Tree.to_html_string(Tree.CACHE, local=False)
			with open(filepath, 'w') as file:
				file.write(Tree.articles_html)
		except Exception as e:
			logging.exception(e)
			raise FeedParserException(e)

	@staticmethod
	def create_pdf() -> None:
		"""Created HTML document and converts it into PDF using wkhtmltopdf, embedding images may take long time."""
		try:
			Tree.create_html(Tree.temp_html_path)
			logging.info("Created html string for converting to pdf")
			logging.info("Creating pdf document using webkit rendering engine and qt")
			pdfkit.from_file(input=Tree.temp_html_path, output_path=Tree.PDF_FILEPATH)
			try:
				logging.info("PDF document created: %s" % Tree.PDF_FILEPATH)
			except Exception as e:
				logging.critical("Failed to create PDF document due to exception: %s", e)
				raise FeedParserException(e)
		except Exception as e:
			logging.exception(e)
			raise FeedParserException(e)

	@staticmethod
	def db_fetch_news(database: sqlite3.Connection, filter_key: str, filter_value: str) -> None:
		"""Selects rows from db according to provided column and value and fetches them
		Returns list of dictionaries containing fetched values"""
		try:
			logging.info("Fetching news articles from database")
			if filter_key is None and filter_value is None:
				sql = f"""SELECT * FROM cached_news"""
			else:
				sql = f"""SELECT * FROM cached_news WHERE {filter_key} LIKE '%{filter_value}%'"""

			logging.info(sql)

			with database:
				cursor = database.cursor()
				cursor.execute(sql)
				data = cursor.fetchall()
			for item in data:
				dict_ = {}
				dict_['date'], dict_['news_feed_title'],dict_['news_src'], dict_['news_title'], dict_['news_date'], dict_['news_description'], dict_['news_url'] = item
				Tree.cache_news(dict_)
			
		except Exception as e:
			logging.exception(e)
			raise FeedParserException(e)

	@staticmethod
	def db_connection(filepath: str) -> sqlite3.Connection:
		"""Connects to or creates the database specified by filepath
		Creates table cached_news if it does not exist
		Returns database connection object"""
		sql = """
		CREATE TABLE IF NOT EXISTS cached_news
				(date TEXT, 
				news_feed_title TEXT,
				news_src TEXT, 
				news_title TEXT, 
				news_date TEXT, 
				news_description TEXT, 
				news_url TEXT)
		"""
		logging.info("Connecting to SQLite database")
		try:
			if os.path.isfile(filepath):
				database = sqlite3.connect(filepath)
			else:
				database = sqlite3.connect(filepath)
				with database:
					cursor = database.cursor()
					cursor.execute(sql)
					logging.info("Creating cached_news table in database: %s" % filepath)
			
			return database
		except Exception as e:
			logging.exception(e)
			raise FeedParserException(e)

	@staticmethod
	def convert_to_json(dict_: dict) -> str:
		"""Method for converting cached news articles to json"""
		logging.info("Converting news articles to json")
		try:
			json_ = json.dumps(dict_)
			return json_
		except Exception as e:
			logging.exception(e)
			raise FeedParserException(e)