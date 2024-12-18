import tkinter as tk
from tkinter import messagebox,Toplevel
from tkcalendar import Calendar
import json
import os
import uuid
from datetime import datetime,timedelta,timezone
from PIL import Image,ImageDraw,ImageTk
import pystray
import threading
import logging
import base64
import requests
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.fernet import Fernet
import tkinter.ttk as ttk
import urllib.parse as urlparse
from io import BytesIO
from html.parser import HTMLParser
import sys

APP_TITLE="QUICK LINKS"
if os.name == 'nt':
    appdata_dir = os.getenv("APPDATA", os.path.expanduser("~"))
    storage_dir = os.path.join(appdata_dir, "QuickLinks")
else:
    home_dir = os.path.expanduser("~")
    storage_dir = os.path.join(home_dir, ".config", "QuickLinks")

if not os.path.exists(storage_dir):
    os.makedirs(storage_dir)

LINKS_FILE = os.path.join(storage_dir, "quick_links.json")
WINDOW_WIDTH=500
WINDOW_HEIGHT=400

class HTMLTitleDescriptionParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_title=False
        self.title=None
        self.description=None
    def handle_starttag(self,tag,attrs):
        if tag.lower()=="title":
            self.in_title=True
        if tag.lower()=="meta":
            d={k.lower():v for k,v in attrs}
            if d.get('name','').lower()=='description':
                self.description=d.get('content','')
    def handle_endtag(self,tag):
        if tag.lower()=="title":
            self.in_title=False
    def handle_data(self,data):
        if self.in_title and self.title is None:
            self.title=data.strip()

class Tooltip:
    def __init__(self,widget):
        self.widget=widget
        self.tipwindow=None
        self.text=""
    def showtip(self,text,x,y):
        self.text=text
        if self.tipwindow or not self.text:
            return
        self.tipwindow=tk.Toplevel(self.widget)
        self.tipwindow.wm_overrideredirect(1)
        self.tipwindow.attributes("-topmost",True)
        label=tk.Label(self.tipwindow,text=self.text,justify=tk.LEFT,background="#ffffe0",relief=tk.SOLID,borderwidth=1,font=("tahoma","9","normal"))
        label.pack(ipadx=1)
        self.tipwindow.wm_geometry("+%d+%d"%(x,y))
    def hidetip(self):
        tw=self.tipwindow
        self.tipwindow=None
        if tw:
            tw.destroy()

class QuickLinksApp:
    def __init__(self,master):
        self.master=master
        self.master.title(APP_TITLE+" 🌐")
        self.master.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}")
        self.master.attributes("-topmost",True)
        try:
            self.icon_image = tk.PhotoImage(file="icon.png")
            self.master.iconphoto(False, self.icon_image)
        except:
            pass
        self.master.bind("<FocusIn>",self.on_focus_in)
        self.master.bind("<FocusOut>",self.on_focus_out)

        style=ttk.Style()
        available_themes=style.theme_names()
        preferred_themes=["clam","alt","default"]
        for th in preferred_themes:
            if th in available_themes:
                style.theme_use(th)
                break

        title_frame=ttk.Frame(self.master)
        title_frame.pack(side=tk.TOP,fill=tk.X,pady=10)
        title_label=ttk.Label(title_frame,text=APP_TITLE,font=("Segoe UI",16,"bold"))
        title_label.pack(pady=5)

        input_frame=ttk.Frame(self.master)
        input_frame.pack(side=tk.TOP,fill=tk.X,padx=10,pady=5)
        self.entry_var=tk.StringVar()
        self.entry=ttk.Entry(input_frame,textvariable=self.entry_var)
        self.entry.pack(side=tk.LEFT,fill=tk.X,expand=True,padx=(0,5))
        add_button=ttk.Button(input_frame,text="➕ Add",command=self.add_link)
        add_button.pack(side=tk.LEFT)

        tree_frame=ttk.Frame(self.master)
        tree_frame.pack(fill=tk.BOTH,expand=True,padx=10,pady=5)
        self.tree=ttk.Treeview(tree_frame,columns=("title"),show="tree",selectmode="browse")
        self.tree.column("#0",stretch=True,width=WINDOW_WIDTH-50)
        self.tree.pack(side=tk.LEFT,fill=tk.BOTH,expand=True)
        self.scrollbar=ttk.Scrollbar(tree_frame,orient="vertical",command=self.tree.yview)
        self.scrollbar.pack(side=tk.RIGHT,fill=tk.Y)
        self.tree.configure(yscrollcommand=self.scrollbar.set)

        self.context_menu=tk.Menu(self.master,tearoff=0)
        self.tree.bind("<Button-3>",self.show_context_menu)
        self.tree.bind("<Button-2>",self.show_context_menu)

        self.tooltip=Tooltip(self.master)
        self.tree.bind("<Motion>",self.on_tree_hover)
        self.tree.bind("<Leave>",lambda e:self.tooltip.hidetip())

        self.links=self.load_links()
        self.favicons={}
        self.update_tree()

        self.master.bind('<Control-Return>',lambda e:self.add_link())
        self.entry.focus()
        self.tree.bind('<Double-Button-1>',self.open_selected_link)
        self.last_motion_event=None

        self.master.protocol("WM_DELETE_WINDOW", self.minimize_to_tray)
        self.tray_icon = None
        self.create_tray_icon()

    def on_focus_in(self,event):
        self.master.attributes("-alpha",1.0)

    def on_focus_out(self,event):
        self.master.attributes("-alpha",0.9)

    def on_tree_hover(self,event):
        self.last_motion_event=event
        x,y,widget=event.x,event.y,event.widget
        item=self.tree.identify_row(y)
        if item:
            idx=self.get_index_from_item(item)
            if idx is not None:
                desc=self.links[idx].get("description","")
                if desc:
                    self.tooltip.showtip(desc,event.x_root+20,event.y_root+20)
                else:
                    self.tooltip.hidetip()
        else:
            self.tooltip.hidetip()

    def show_context_menu(self,event):
        item=self.tree.identify_row(event.y)
        self.context_menu.delete(0,tk.END)
        if item:
            self.context_menu.add_command(label="🗑 Remove Selected",command=self.remove_selected_link)
            idx=self.get_index_from_item(item)
            if idx is not None:
                link_data=self.links[idx]
                if link_data.get("is_onenote"):
                    self.context_menu.add_separator()
                    self.context_menu.add_command(label="📔 Open Notebook",command=lambda:self.open_notebook(idx))
                    self.context_menu.add_command(label="📄 Open Section",command=lambda:self.open_section(idx))
            self.context_menu.post(event.x_root,event.y_root)

    def open_notebook(self,idx):
        link_data=self.links[idx]
        onenote_link=link_data["url"]
        parsed=urlparse.urlparse(onenote_link[8:])
        parts=parsed.path.split('/')
        if len(parts)>3:
            notebook_url=f"onenote:{parsed.scheme}://{parsed.netloc}/"+"/".join(parts[1:4])
        else:
            notebook_url=onenote_link
        os.startfile(notebook_url)

    def open_section(self,idx):
        link_data=self.links[idx]
        onenote_link=link_data["url"]
        os.startfile(onenote_link)

    def get_index_from_item(self,item):
        try:
            return int(item)
        except:
            return None

    def load_links(self):
        if os.path.exists(LINKS_FILE):
            try:
                with open(LINKS_FILE,"r",encoding="utf-8") as f:
                    return json.load(f)
            except:
                return []
        return []

    def save_links(self):
        with open(LINKS_FILE,"w",encoding="utf-8") as f:
            json.dump(self.links,f,indent=4)

    def update_tree(self):
        self.tree.delete(*self.tree.get_children())
        self.favicons.clear()
        for i,link_data in enumerate(self.links):
            icon=None
            if link_data.get("favicon_data"):
                img=Image.open(BytesIO(base64.b64decode(link_data["favicon_data"])))
                img=img.resize((16,16),Image.Resampling.LANCZOS)
                tkimg=ImageTk.PhotoImage(img)
                self.favicons[i]=tkimg
                icon=tkimg
            title=link_data.get("title",link_data.get("url",""))
            if icon:
                self.tree.insert("", "end",iid=str(i),text=title,image=icon)
            else:
                self.tree.insert("", "end",iid=str(i),text=title)

    def add_link(self):
        raw_input=self.entry_var.get().strip()
        if raw_input:
            url=None
            if "onenote:" in raw_input:
                start=raw_input.find("onenote:")
                url=raw_input[start:].strip()
            else:
                lines=raw_input.split('\n')
                url=lines[0].strip()
            link_data=self.fetch_metadata(url)
            self.links.append(link_data)
            self.save_links()
            self.update_tree()
            self.entry_var.set("")
        else:
            messagebox.showwarning("⚠️ Warning","Please enter a valid link.")

    def remove_selected_link(self):
        selection=self.tree.selection()
        if selection:
            index=int(selection[0])
            confirm=messagebox.askyesno("Confirm Delete","Remove this link?")
            if confirm:
                self.links.pop(index)
                self.save_links()
                self.update_tree()

    def open_selected_link(self,event=None):
        selection=self.tree.selection()
        if selection:
            index=int(selection[0])
            link=self.links[index]["url"]
            try:
                os.startfile(link)
            except Exception as e:
                messagebox.showerror("❌ Error",f"Could not open the link.\n{e}")
        else:
            messagebox.showinfo("ℹ️ Info","No link selected.")

    def fetch_metadata(self,url):
        link_data={"url":url,"title":url,"description":"","favicon_data":None,"is_onenote":False}
        if url.startswith("onenote:"):
            link_data["is_onenote"]=True
            onenote_url=url[8:]
            parsed=urlparse.urlparse(onenote_url)
            parts=parsed.path.split('/')
            notebook_name=""
            section_name=""
            if len(parts)>=4:
                notebook_name=urlparse.unquote(parts[3])
            # Find a .one file in parts to get section name
            # For example: "Scripts.one" => section_name = "Scripts"
            for p in parts:
                if p.lower().endswith(".one"):
                    section_name=urlparse.unquote(p).rsplit('.',1)[0]
                    break
            frag=parsed.fragment
            # Parse fragment: could have page-id, section-id, etc.
            # Example:
            # Page: #Get%20AD%20Users&section-id={...}&page-id={...}&end
            # Section: #section-id={...}&end
            # If page-id present => page
            # If section-id present but no page-id => section
            # The portion before section-id is page name if page-id is present
            params = frag.split('&')
            fragment_params = {}
            # The first part might be a page name if no '=' in it
            first_part = params[0] if '=' not in params[0] else None
            for param in params:
                if '=' in param:
                    k,v=param.split('=',1)
                    fragment_params[k.lower()]=v

            section_id = fragment_params.get('section-id',None)
            page_id = fragment_params.get('page-id',None)

            # Determine title
            if section_id and page_id:
                # It's a page
                page_name = urlparse.unquote(first_part) if first_part else section_name
                icon_emoji="📄"
                display_title=f"{icon_emoji} {page_name}"
                # Add extra info for hierarchy
                if notebook_name and section_name and page_name!=section_name:
                    display_title=f"{display_title} ({notebook_name}/{section_name})"
            elif section_id and not page_id:
                # It's a section
                icon_emoji="📁"
                display_title=f"{icon_emoji} {section_name if section_name else url}"
                if notebook_name and section_name and section_name!=notebook_name:
                    display_title=f"{display_title} ({notebook_name})"
            else:
                # Just a notebook link or something else
                icon_emoji="📒"
                display_title=f"{icon_emoji} {notebook_name if notebook_name else url}"

            link_data["title"]=display_title
            link_data["description"]=url
            return link_data

        if url.startswith("http"):
            try:
                resp=requests.get(url,timeout=5)
                if resp.status_code==200:
                    parser=HTMLTitleDescriptionParser()
                    parser.feed(resp.text)
                    if parser.title:
                        link_data["title"]=parser.title
                    if parser.description:
                        link_data["description"]=parser.description
                    favicon_url=self.find_favicon_url(resp.text,url)
                    if favicon_url:
                        fav_resp=requests.get(favicon_url,timeout=5)
                        if fav_resp.status_code==200:
                            favicon_data=base64.b64encode(fav_resp.content).decode('utf-8')
                            link_data["favicon_data"]=favicon_data
            except:
                pass
        return link_data

    def find_favicon_url(self,html,base_url):
        lower_html=html.lower()
        icon_pos=lower_html.find('rel="icon"')
        href=None
        if icon_pos!=-1:
            start=lower_html.rfind('<link',0,icon_pos)
            if start!=-1:
                snippet=html[start:lower_html.find('>',start)+1]
                href_pos=snippet.lower().find('href=')
                if href_pos!=-1:
                    quote_char=snippet[href_pos+5]
                    if quote_char in ['"',"'"]:
                        end_pos=snippet.find(quote_char,href_pos+6)
                        if end_pos!=-1:
                            href=snippet[href_pos+6:end_pos]
                    else:
                        end_pos_space=snippet.find(' ',href_pos+5)
                        if end_pos_space==-1:
                            end_pos_space=snippet.find('>',href_pos+5)
                        if end_pos_space!=-1:
                            href=snippet[href_pos+5:end_pos_space]
        if not href:
            parsed=urlparse.urlparse(base_url)
            href=f"{parsed.scheme}://{parsed.netloc}/favicon.ico"
        return urlparse.urljoin(base_url,href)

    def create_tray_icon(self):
        image=Image.open("icon.png")
        menu=pystray.Menu(pystray.MenuItem("Open Quick Links",self.on_tray_open),pystray.MenuItem("Quit",self.on_tray_quit))
        self.tray_icon=pystray.Icon(APP_TITLE,image,APP_TITLE,menu)

    def minimize_to_tray(self):
        self.master.withdraw()
        threading.Thread(target=self.tray_icon.run,daemon=True).start()

    def on_tray_open(self,icon,item):
        self.master.deiconify()
        self.master.attributes("-topmost",True)
        self.master.attributes("-topmost",False)
        self.tray_icon.stop()

    def on_tray_quit(self,icon,item):
        self.tray_icon.stop()
        self.master.destroy()

def main():
    root=tk.Tk()
    app=QuickLinksApp(root)
    root.mainloop()

if __name__=="__main__":
    main()
